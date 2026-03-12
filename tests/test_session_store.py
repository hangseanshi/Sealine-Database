"""
Unit tests for server.sessions.store module.

Tests cover:
  - Session creation (default and custom model)
  - Session retrieval (existing and missing sessions)
  - Session deletion (existing and non-existent sessions)
  - Session listing (all active sessions as metadata)
  - TTL expiry and cleanup_expired
  - Thread safety (concurrent access to create/get/delete)
  - FileRecord handling (creation, metadata serialization)
  - Session attribute updates (touch, messages, usage counters)
  - Session.to_metadata() output format and content
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from server.sessions.store import SessionStore, Session, FileRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    """Create a SessionStore with a very long cleanup interval (no auto-cleanup)."""
    s = SessionStore(ttl_hours=2, cleanup_interval_seconds=999999)
    yield s


@pytest.fixture
def tmp_file_store():
    """Create a temporary directory for file storage."""
    d = tempfile.mkdtemp(prefix="test_session_files_")
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests: Session creation
# ---------------------------------------------------------------------------


class TestSessionCreation:
    """Test creating sessions."""

    def test_create_returns_session(self, store):
        session = store.create()
        assert isinstance(session, Session)

    def test_create_generates_unique_id(self, store):
        s1 = store.create()
        s2 = store.create()
        assert s1.session_id != s2.session_id

    def test_create_default_model(self, store):
        session = store.create()
        assert session.model == "claude-haiku-4-5"

    def test_create_custom_model(self, store):
        session = store.create(model="claude-sonnet-4-20250514")
        assert session.model == "claude-sonnet-4-20250514"

    def test_create_sets_created_at(self, store):
        before = datetime.now(timezone.utc)
        session = store.create()
        after = datetime.now(timezone.utc)
        assert before <= session.created_at <= after

    def test_create_sets_last_active(self, store):
        session = store.create()
        assert session.last_active is not None

    def test_create_initializes_empty_messages(self, store):
        session = store.create()
        assert session.messages == []

    def test_create_initializes_zero_counters(self, store):
        session = store.create()
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.cache_hits == 0
        assert session.sql_calls == 0

    def test_create_initializes_empty_files(self, store):
        session = store.create()
        assert session.files == []

    def test_create_multiple_sessions(self, store):
        sessions = [store.create() for _ in range(10)]
        ids = {s.session_id for s in sessions}
        assert len(ids) == 10


# ---------------------------------------------------------------------------
# Tests: Session retrieval
# ---------------------------------------------------------------------------


class TestSessionRetrieval:
    """Test getting sessions by ID."""

    def test_get_existing_session(self, store):
        created = store.create()
        retrieved = store.get(created.session_id)
        assert retrieved is created  # same object

    def test_get_nonexistent_session_raises_key_error(self, store):
        with pytest.raises(KeyError):
            store.get("nonexistent-id")

    def test_get_preserves_session_data(self, store):
        session = store.create()
        session.messages.append({"role": "user", "content": "hello"})
        session.total_input_tokens = 100
        retrieved = store.get(session.session_id)
        assert len(retrieved.messages) == 1
        assert retrieved.total_input_tokens == 100

    def test_get_after_deletion_raises(self, store):
        session = store.create()
        sid = session.session_id
        store.delete(sid)
        with pytest.raises(KeyError):
            store.get(sid)


# ---------------------------------------------------------------------------
# Tests: Session deletion
# ---------------------------------------------------------------------------


class TestSessionDeletion:
    """Test deleting sessions."""

    def test_delete_existing_session(self, store):
        session = store.create()
        sid = session.session_id
        store.delete(sid)
        with pytest.raises(KeyError):
            store.get(sid)

    def test_delete_nonexistent_session_no_error(self, store):
        # Should not raise
        store.delete("does-not-exist")

    def test_delete_cleans_up_files(self, store, tmp_file_store):
        session = store.create()
        # Create a real file
        filepath = os.path.join(tmp_file_store, "test_file.txt")
        with open(filepath, "w") as f:
            f.write("test content")
        session.files.append(FileRecord(
            file_id="f1",
            filename="test_file.txt",
            file_type="text/plain",
            file_path=filepath,
        ))
        store.delete(session.session_id)
        assert not os.path.exists(filepath)

    def test_delete_missing_file_no_error(self, store):
        session = store.create()
        session.files.append(FileRecord(
            file_id="f1",
            filename="ghost.txt",
            file_type="text/plain",
            file_path="/nonexistent/path/ghost.txt",
        ))
        # Should not raise even though file doesn't exist
        store.delete(session.session_id)

    def test_delete_reduces_session_count(self, store):
        s1 = store.create()
        s2 = store.create()
        assert len(store.list_sessions()) == 2
        store.delete(s1.session_id)
        assert len(store.list_sessions()) == 1


# ---------------------------------------------------------------------------
# Tests: Session listing
# ---------------------------------------------------------------------------


class TestSessionListing:
    """Test listing all active sessions."""

    def test_list_empty_store(self, store):
        sessions = store.list_sessions()
        assert sessions == []

    def test_list_one_session(self, store):
        store.create()
        sessions = store.list_sessions()
        assert len(sessions) == 1

    def test_list_multiple_sessions(self, store):
        for _ in range(5):
            store.create()
        sessions = store.list_sessions()
        assert len(sessions) == 5

    def test_list_returns_metadata_dicts(self, store):
        store.create()
        sessions = store.list_sessions()
        meta = sessions[0]
        assert isinstance(meta, dict)
        assert "session_id" in meta
        assert "created_at" in meta
        assert "last_active" in meta
        assert "model" in meta
        assert "message_count" in meta
        assert "usage" in meta

    def test_list_after_deletion(self, store):
        s1 = store.create()
        s2 = store.create()
        store.delete(s1.session_id)
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == s2.session_id


# ---------------------------------------------------------------------------
# Tests: TTL expiry
# ---------------------------------------------------------------------------


class TestTTLExpiry:
    """Test automatic expiry of old sessions."""

    def test_cleanup_expired_removes_old_sessions(self, store):
        session = store.create()
        # Manually set last_active to 3 hours ago (TTL is 2 hours)
        session.last_active = datetime.now(timezone.utc) - timedelta(hours=3)
        removed = store.cleanup_expired()
        assert removed == 1
        with pytest.raises(KeyError):
            store.get(session.session_id)

    def test_cleanup_expired_keeps_recent_sessions(self, store):
        session = store.create()
        # Session was just created, should not be expired
        removed = store.cleanup_expired()
        assert removed == 0
        # Session should still be accessible
        store.get(session.session_id)

    def test_cleanup_with_custom_ttl(self, store):
        session = store.create()
        session.last_active = datetime.now(timezone.utc) - timedelta(hours=1)
        # Default TTL is 2 hours; with custom TTL of 0.5 hours, it should expire
        removed = store.cleanup_expired(ttl_hours=0)
        assert removed == 1

    def test_cleanup_mixed_expired_and_active(self, store):
        old = store.create()
        old.last_active = datetime.now(timezone.utc) - timedelta(hours=5)
        new = store.create()
        removed = store.cleanup_expired()
        assert removed == 1
        with pytest.raises(KeyError):
            store.get(old.session_id)
        # New session should still exist
        store.get(new.session_id)

    def test_cleanup_returns_count(self, store):
        for _ in range(3):
            s = store.create()
            s.last_active = datetime.now(timezone.utc) - timedelta(hours=10)
        removed = store.cleanup_expired()
        assert removed == 3


# ---------------------------------------------------------------------------
# Tests: Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Test thread-safe concurrent access."""

    def test_concurrent_creates(self, store):
        """Multiple threads creating sessions simultaneously."""
        results = []
        errors = []

        def create_session():
            try:
                s = store.create()
                results.append(s.session_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_session) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert len(results) == 50
        assert len(set(results)) == 50  # all unique

    def test_concurrent_creates_and_deletes(self, store):
        """Threads creating and deleting sessions concurrently."""
        errors = []
        created_ids = []

        def create_and_delete():
            try:
                s = store.create()
                created_ids.append(s.session_id)
                time.sleep(0.001)  # tiny delay
                store.delete(s.session_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_and_delete) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0

    def test_concurrent_reads(self, store):
        """Multiple threads reading the same session."""
        session = store.create()
        session.messages.append({"role": "user", "content": "test"})
        errors = []
        results = []

        def read_session():
            try:
                s = store.get(session.session_id)
                results.append(len(s.messages))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_session) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert all(r == 1 for r in results)

    def test_concurrent_list(self, store):
        """Multiple threads listing sessions while others create."""
        errors = []

        def create_sessions():
            try:
                for _ in range(10):
                    store.create()
            except Exception as e:
                errors.append(e)

        def list_sessions():
            try:
                for _ in range(10):
                    store.list_sessions()
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=create_sessions) for _ in range(5)] +
            [threading.Thread(target=list_sessions) for _ in range(5)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Tests: FileRecord handling
# ---------------------------------------------------------------------------


class TestFileRecord:
    """Test the FileRecord dataclass."""

    def test_create_file_record(self):
        fr = FileRecord(
            file_id="abc123",
            filename="report.pdf",
            file_type="application/pdf",
            file_path="/tmp/report.pdf",
        )
        assert fr.file_id == "abc123"
        assert fr.filename == "report.pdf"
        assert fr.file_type == "application/pdf"
        assert fr.file_path == "/tmp/report.pdf"

    def test_default_created_at(self):
        before = datetime.now(timezone.utc)
        fr = FileRecord(
            file_id="id", filename="f", file_type="t", file_path="/p"
        )
        after = datetime.now(timezone.utc)
        assert before <= fr.created_at <= after

    def test_default_size_bytes(self):
        fr = FileRecord(
            file_id="id", filename="f", file_type="t", file_path="/p"
        )
        assert fr.size_bytes == 0

    def test_custom_size_bytes(self):
        fr = FileRecord(
            file_id="id", filename="f", file_type="t", file_path="/p",
            size_bytes=1024,
        )
        assert fr.size_bytes == 1024

    def test_session_files_list(self, store):
        session = store.create()
        fr = FileRecord(
            file_id="f1",
            filename="chart.png",
            file_type="image/png",
            file_path="/tmp/chart.png",
            size_bytes=5000,
        )
        session.files.append(fr)
        assert len(session.files) == 1
        assert session.files[0].file_id == "f1"


# ---------------------------------------------------------------------------
# Tests: Session attribute updates
# ---------------------------------------------------------------------------


class TestSessionUpdates:
    """Test updating session attributes."""

    def test_touch_updates_last_active(self, store):
        session = store.create()
        old_active = session.last_active
        time.sleep(0.01)
        session.touch()
        assert session.last_active > old_active

    def test_append_message(self, store):
        session = store.create()
        session.messages.append({"role": "user", "content": "hello"})
        session.messages.append({"role": "assistant", "content": "hi"})
        assert len(session.messages) == 2

    def test_increment_token_counters(self, store):
        session = store.create()
        session.total_input_tokens += 100
        session.total_output_tokens += 200
        assert session.total_input_tokens == 100
        assert session.total_output_tokens == 200

    def test_increment_cache_hits(self, store):
        session = store.create()
        session.cache_hits += 1
        assert session.cache_hits == 1

    def test_increment_sql_calls(self, store):
        session = store.create()
        session.sql_calls += 5
        assert session.sql_calls == 5


# ---------------------------------------------------------------------------
# Tests: Session.to_metadata()
# ---------------------------------------------------------------------------


class TestSessionMetadata:
    """Test the to_metadata() serialization."""

    def test_metadata_has_required_keys(self, store):
        session = store.create()
        meta = session.to_metadata()
        required_keys = [
            "session_id", "created_at", "last_active", "model",
            "message_count", "user_turns", "usage", "files_generated",
        ]
        for key in required_keys:
            assert key in meta, f"Missing key: {key}"

    def test_metadata_usage_keys(self, store):
        session = store.create()
        meta = session.to_metadata()
        usage = meta["usage"]
        assert "input_tokens" in usage
        assert "output_tokens" in usage
        assert "cache_hits" in usage
        assert "sql_calls" in usage

    def test_metadata_reflects_messages(self, store):
        session = store.create()
        session.messages.append({"role": "user", "content": "q1"})
        session.messages.append({"role": "assistant", "content": "a1"})
        meta = session.to_metadata()
        assert meta["message_count"] == 2

    def test_metadata_user_turns_excludes_tool_results(self, store):
        session = store.create()
        session.messages.append({"role": "user", "content": "question"})
        session.messages.append({"role": "assistant", "content": "answer"})
        # Tool result message (should not count as user turn)
        session.messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "res"}]
        })
        meta = session.to_metadata()
        assert meta["user_turns"] == 1

    def test_metadata_files_generated(self, store):
        session = store.create()
        session.files.append(FileRecord(
            file_id="f1",
            filename="report.pdf",
            file_type="application/pdf",
            file_path="/tmp/report.pdf",
            size_bytes=2048,
        ))
        meta = session.to_metadata()
        assert len(meta["files_generated"]) == 1
        f_meta = meta["files_generated"][0]
        assert f_meta["file_id"] == "f1"
        assert f_meta["filename"] == "report.pdf"
        assert f_meta["type"] == "application/pdf"
        assert f_meta["size_bytes"] == 2048
        assert "created_at" in f_meta

    def test_metadata_timestamps_are_iso(self, store):
        session = store.create()
        meta = session.to_metadata()
        # Should be parseable ISO format
        datetime.fromisoformat(meta["created_at"])
        datetime.fromisoformat(meta["last_active"])

    def test_metadata_usage_counters_reflect_updates(self, store):
        session = store.create()
        session.total_input_tokens = 500
        session.total_output_tokens = 300
        session.cache_hits = 2
        session.sql_calls = 10
        meta = session.to_metadata()
        assert meta["usage"]["input_tokens"] == 500
        assert meta["usage"]["output_tokens"] == 300
        assert meta["usage"]["cache_hits"] == 2
        assert meta["usage"]["sql_calls"] == 10

    def test_metadata_model(self, store):
        session = store.create(model="claude-opus-4-20250514")
        meta = session.to_metadata()
        assert meta["model"] == "claude-opus-4-20250514"

    def test_metadata_no_messages_in_output(self, store):
        """to_metadata should NOT include the full message history."""
        session = store.create()
        session.messages.append({"role": "user", "content": "secret stuff"})
        meta = session.to_metadata()
        assert "messages" not in meta


# ---------------------------------------------------------------------------
# Tests: Session dataclass defaults
# ---------------------------------------------------------------------------


class TestSessionDefaults:
    """Test Session dataclass default values when created directly."""

    def test_direct_session_creation(self):
        session = Session(session_id="test-id-123")
        assert session.session_id == "test-id-123"
        assert session.model == "claude-haiku-4-5"
        assert session.messages == []
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.cache_hits == 0
        assert session.sql_calls == 0
        assert session.files == []
