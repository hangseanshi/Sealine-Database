"""
In-memory session store for Sealine Data Chat.

Provides thread-safe session creation, retrieval, deletion, and automatic
cleanup of expired sessions.  See PRD Section 9 for the design.

Sessions are ephemeral — they survive only in-process and are lost on
server restart.  This is acceptable for V1 (1-5 internal users).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileRecord:
    """Metadata for a generated file associated with a session."""

    file_id: str
    """Short UUID identifying the file."""

    filename: str
    """Original filename (e.g. "transit_report.xlsx")."""

    file_type: str
    """MIME type (e.g. "application/pdf")."""

    file_path: str
    """Absolute path to the file on disk."""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """UTC timestamp of file creation."""

    size_bytes: int = 0
    """File size in bytes."""


@dataclass
class Session:
    """A single chat session with conversation history and usage tracking."""

    session_id: str
    """UUID-based session identifier."""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """UTC timestamp when the session was created."""

    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """UTC timestamp of the most recent activity (message sent)."""

    messages: list[dict] = field(default_factory=list)
    """Full conversation history in Anthropic messages format."""

    model: str = "claude-haiku-4-5"
    """The Claude model used for this session."""

    total_input_tokens: int = 0
    """Cumulative input tokens consumed across all messages."""

    total_output_tokens: int = 0
    """Cumulative output tokens generated across all messages."""

    cache_hits: int = 0
    """Number of prompt cache hits in this session."""

    sql_calls: int = 0
    """Number of SQL tool calls executed in this session."""

    files: list[FileRecord] = field(default_factory=list)
    """Generated files metadata for this session."""

    def touch(self) -> None:
        """Update last_active timestamp to now."""
        self.last_active = datetime.now(timezone.utc)

    def to_metadata(self) -> dict:
        """Return a JSON-serialisable metadata dict (no full message history)."""
        # Count user turns (exclude tool_result pseudo-user messages)
        user_turns = sum(
            1
            for m in self.messages
            if m.get("role") == "user"
            and not (
                isinstance(m.get("content"), list)
                and m["content"]
                and isinstance(m["content"][0], dict)
                and m["content"][0].get("type") == "tool_result"
            )
        )

        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "model": self.model,
            "message_count": len(self.messages),
            "user_turns": user_turns,
            "usage": {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "cache_hits": self.cache_hits,
                "sql_calls": self.sql_calls,
            },
            "files_generated": [
                {
                    "file_id": f.file_id,
                    "filename": f.filename,
                    "type": f.file_type,
                    "created_at": f.created_at.isoformat(),
                    "size_bytes": f.size_bytes,
                }
                for f in self.files
            ],
        }


# ---------------------------------------------------------------------------
#  Session Store
# ---------------------------------------------------------------------------

class SessionStore:
    """
    Thread-safe in-memory session store.

    Provides CRUD operations and automatic background cleanup of sessions
    that have been inactive longer than *ttl_hours*.
    """

    def __init__(
        self,
        ttl_hours: int = 2,
        file_store_path: str = "",
        cleanup_interval_seconds: int = 600,
    ):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._ttl_hours = ttl_hours
        self._file_store_path = file_store_path

        # Start background cleanup daemon thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            args=(cleanup_interval_seconds,),
            daemon=True,
            name="session-cleanup",
        )
        self._cleanup_thread.start()
        logger.info(
            "SessionStore started (TTL=%dh, cleanup every %ds)",
            ttl_hours,
            cleanup_interval_seconds,
        )

    # -- CRUD ---------------------------------------------------------------

    def create(self, model: str = "claude-haiku-4-5") -> Session:
        """Create and store a new session. Returns the Session object."""
        session = Session(
            session_id=str(uuid.uuid4()),
            model=model,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        logger.info("Session created: %s", session.session_id)
        return session

    def get(self, session_id: str) -> Session:
        """
        Retrieve a session by ID.

        Raises KeyError if the session does not exist (expired or invalid).
        """
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        return session

    def delete(self, session_id: str) -> None:
        """
        Delete a session and clean up its associated files on disk.

        Does nothing (no error) if the session does not exist.
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return
        self._cleanup_session_files(session)
        logger.info("Session deleted: %s", session_id)

    def list_sessions(self) -> list[dict]:
        """Return metadata dicts for all active sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
        return [s.to_metadata() for s in sessions]

    def cleanup_expired(self, ttl_hours: int | None = None) -> int:
        """
        Remove sessions that have been inactive for longer than *ttl_hours*.

        Returns the number of sessions deleted.
        """
        ttl = ttl_hours if ttl_hours is not None else self._ttl_hours
        now = datetime.now(timezone.utc)

        # Pop expired sessions atomically under the lock to prevent a race
        # where a session could be reactivated between identifying it as
        # expired and actually deleting it.
        expired_sessions: list[Session] = []
        with self._lock:
            expired_ids = [
                sid for sid, s in self._sessions.items()
                if (now - s.last_active).total_seconds() / 3600.0 >= ttl
            ]
            for sid in expired_ids:
                session = self._sessions.pop(sid, None)
                if session is not None:
                    expired_sessions.append(session)

        # Clean up files outside the lock (safe — sessions already removed)
        for session in expired_sessions:
            self._cleanup_session_files(session)

        if expired_sessions:
            logger.info("Cleaned up %d expired session(s)", len(expired_sessions))

        return len(expired_sessions)

    # -- Internal helpers ---------------------------------------------------

    def _cleanup_session_files(self, session: Session) -> None:
        """Remove all generated files associated with a session."""
        for file_rec in session.files:
            try:
                if os.path.isfile(file_rec.file_path):
                    os.remove(file_rec.file_path)
                    logger.debug("Removed file: %s", file_rec.file_path)
            except OSError as exc:
                logger.warning(
                    "Failed to remove file %s: %s", file_rec.file_path, exc
                )

    def _cleanup_loop(self, interval: int) -> None:
        """Background thread loop that runs cleanup_expired every *interval* seconds."""
        import time

        while True:
            time.sleep(interval)
            try:
                self.cleanup_expired()
            except Exception:
                logger.exception("Error in session cleanup loop")
