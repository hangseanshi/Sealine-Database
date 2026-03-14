"""
Unit tests for the Sessions route (server/routes/sessions.py).

Tests:
  - POST /api/sessions -> 201 with session_id, created_at, model, db_enabled
  - GET  /api/sessions/<id> -> 200 with session metadata
  - GET  /api/sessions/<nonexistent> -> 404
  - DELETE /api/sessions/<id> -> 200
  - DELETE /api/sessions/<nonexistent> -> 404
"""

import json
from datetime import datetime, timezone

import pytest


class TestCreateSession:
    """POST /api/sessions"""

    def test_create_session_returns_201(self, client):
        """Creating a session should return 201 Created."""
        resp = client.post("/api/sessions")
        assert resp.status_code == 201

    def test_create_session_returns_session_id(self, client):
        """The response should contain a session_id."""
        resp = client.post("/api/sessions")
        data = resp.get_json()
        assert "session_id" in data
        assert isinstance(data["session_id"], str)
        assert len(data["session_id"]) > 0

    def test_create_session_returns_created_at(self, client):
        """The response should contain a created_at ISO timestamp."""
        resp = client.post("/api/sessions")
        data = resp.get_json()
        assert "created_at" in data
        # Should be a valid ISO timestamp
        dt = datetime.fromisoformat(data["created_at"])
        assert dt is not None

    def test_create_session_returns_model(self, client):
        """The response should contain the model name."""
        resp = client.post("/api/sessions")
        data = resp.get_json()
        assert "model" in data
        assert data["model"] == "claude-haiku-4-5"

    def test_create_session_returns_db_enabled(self, client):
        """The response should contain db_enabled flag."""
        resp = client.post("/api/sessions")
        data = resp.get_json()
        assert "db_enabled" in data
        assert isinstance(data["db_enabled"], bool)

    def test_create_multiple_sessions_get_unique_ids(self, client):
        """Each created session should have a unique ID."""
        resp1 = client.post("/api/sessions")
        resp2 = client.post("/api/sessions")
        id1 = resp1.get_json()["session_id"]
        id2 = resp2.get_json()["session_id"]
        assert id1 != id2


class TestGetSession:
    """GET /api/sessions/<session_id>"""

    def test_get_existing_session_returns_200(self, client, created_session):
        """Retrieving an existing session should return 200."""
        resp = client.get(f"/api/sessions/{created_session.session_id}")
        assert resp.status_code == 200

    def test_get_session_returns_correct_fields(self, client, created_session):
        """The response should include all expected metadata fields."""
        resp = client.get(f"/api/sessions/{created_session.session_id}")
        data = resp.get_json()

        assert data["session_id"] == created_session.session_id
        assert "created_at" in data
        assert "model" in data
        assert "message_count" in data
        assert "usage" in data
        assert "files_generated" in data

    def test_get_session_usage_fields(self, client, created_session):
        """The usage object should contain input_tokens, output_tokens, cache_hits, sql_calls."""
        resp = client.get(f"/api/sessions/{created_session.session_id}")
        data = resp.get_json()
        usage = data["usage"]

        assert "input_tokens" in usage
        assert "output_tokens" in usage
        assert "cache_hits" in usage
        assert "sql_calls" in usage

    def test_get_session_message_count_zero(self, client, created_session):
        """A new session should have message_count 0."""
        resp = client.get(f"/api/sessions/{created_session.session_id}")
        data = resp.get_json()
        assert data["message_count"] == 0

    def test_get_session_message_count_with_messages(self, client, created_session):
        """Message count should only count user messages with string content."""
        created_session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "another question"},
        ]
        resp = client.get(f"/api/sessions/{created_session.session_id}")
        data = resp.get_json()
        assert data["message_count"] == 2

    def test_get_session_excludes_tool_result_messages(self, client, created_session):
        """tool_result pseudo-user messages should NOT be counted."""
        created_session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}]},
        ]
        resp = client.get(f"/api/sessions/{created_session.session_id}")
        data = resp.get_json()
        assert data["message_count"] == 1

    def test_get_nonexistent_session_returns_404(self, client):
        """Retrieving a non-existent session should return 404."""
        resp = client.get("/api/sessions/nonexistent-id-12345")
        assert resp.status_code == 404

    def test_get_nonexistent_session_error_format(self, client):
        """404 response should include error, code, and status fields."""
        resp = client.get("/api/sessions/nonexistent-id-12345")
        data = resp.get_json()
        assert "error" in data
        assert data["code"] == "SESSION_NOT_FOUND"
        assert data["status"] == 404

    def test_get_session_with_files(self, client, created_session, sample_file_record):
        """Session with files should include files_generated with correct fields."""
        fr = sample_file_record(filename="report.xlsx")
        created_session.files.append(fr)

        resp = client.get(f"/api/sessions/{created_session.session_id}")
        data = resp.get_json()

        assert len(data["files_generated"]) == 1
        file_info = data["files_generated"][0]
        assert file_info["file_id"] == fr.file_id
        assert file_info["filename"] == "report.xlsx"
        assert "type" in file_info
        assert "created_at" in file_info


class TestDeleteSession:
    """DELETE /api/sessions/<session_id>"""

    def test_delete_existing_session_returns_200(self, client, created_session):
        """Deleting an existing session should return 200."""
        resp = client.delete(f"/api/sessions/{created_session.session_id}")
        assert resp.status_code == 200

    def test_delete_session_response_body(self, client, created_session):
        """The response should confirm deletion with status and session_id."""
        sid = created_session.session_id
        resp = client.delete(f"/api/sessions/{sid}")
        data = resp.get_json()
        assert data["status"] == "deleted"
        assert data["session_id"] == sid

    def test_delete_session_removes_from_store(self, client, created_session, session_store):
        """After deletion, the session should no longer exist in the store."""
        sid = created_session.session_id
        client.delete(f"/api/sessions/{sid}")

        # Trying to get the deleted session should 404
        resp = client.get(f"/api/sessions/{sid}")
        assert resp.status_code == 404

    def test_delete_nonexistent_session_returns_404(self, client):
        """Deleting a non-existent session should return 404."""
        resp = client.delete("/api/sessions/nonexistent-id-12345")
        assert resp.status_code == 404

    def test_delete_nonexistent_session_error_format(self, client):
        """404 response should include the correct error code."""
        resp = client.delete("/api/sessions/nonexistent-id-12345")
        data = resp.get_json()
        assert data["code"] == "SESSION_NOT_FOUND"
        assert data["status"] == 404

    def test_delete_session_cleans_up_files(self, client, created_session, sample_file_record):
        """Deleting a session should remove associated files from disk."""
        import os
        fr = sample_file_record(filename="to_delete.xlsx")
        created_session.files.append(fr)
        assert os.path.exists(fr.file_path)

        client.delete(f"/api/sessions/{created_session.session_id}")

        # File should be cleaned up by the store.delete() call
        # Note: The route calls store.delete() which calls _cleanup_session_files
        # The file may or may not exist depending on implementation details.
        # The route also manually removes files before calling store.delete().
        # Either way, the file should be removed.
        assert not os.path.exists(fr.file_path)
