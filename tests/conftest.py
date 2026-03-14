"""
Shared pytest fixtures for Sealine Data Chat route tests.

Provides:
  - A Flask application (with mocked dependencies)
  - A Flask test client
  - A real SessionStore (in-memory, no cleanup thread)
  - A mock SealineAgent that never makes real API calls
  - A mock pyodbc module
  - Helpers for creating sessions and file records
"""

import os
import sys
import json
import types
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


# ---------------------------------------------------------------------------
#  Fake pyodbc module — injected into sys.modules so that
#  ``import pyodbc`` succeeds without the real ODBC driver.
# ---------------------------------------------------------------------------
_fake_pyodbc = types.ModuleType("pyodbc")
_fake_pyodbc.connect = MagicMock(return_value=MagicMock())
sys.modules["pyodbc"] = _fake_pyodbc


# ---------------------------------------------------------------------------
#  Fake server.core.agent module — injected into sys.modules so that
#  ``from server.core.agent import SealineAgent`` inside generate() and
#  teams_webhook() gets our mock instead of trying to import the real
#  module (which has import-chain issues on Python 3.9 due to
#  ``Config | None`` syntax in server/config.py).
# ---------------------------------------------------------------------------

# First ensure the real server.core package is imported so it's in sys.modules
# as a proper package.  Then we only override server.core.agent (not the
# server.core package itself).
import server.core  # noqa: E402

_fake_agent_module = types.ModuleType("server.core.agent")
_fake_agent_module.SealineAgent = MagicMock()
sys.modules["server.core.agent"] = _fake_agent_module
# Also set it as an attribute on the parent package so that
# ``patch("server.core.agent.SealineAgent")`` can resolve the dotted path.
server.core.agent = _fake_agent_module


# ---------------------------------------------------------------------------
#  A SessionStore that does NOT start the background cleanup thread.
#  (Avoids creating daemon threads during tests.)
# ---------------------------------------------------------------------------
from server.sessions.store import SessionStore, Session, FileRecord


class TestSessionStore(SessionStore):
    """A SessionStore subclass that skips the background cleanup thread."""

    def __init__(self, ttl_hours=2, file_store_path="", cleanup_interval_seconds=600):
        # Bypass the parent __init__ entirely to avoid starting the thread.
        self._sessions = {}
        self._lock = threading.Lock()
        self._ttl_hours = ttl_hours
        self._file_store_path = file_store_path
        # No cleanup thread is started.


# ---------------------------------------------------------------------------
#  Flask app factory for tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def file_store_dir(tmp_path):
    """Create a temporary directory for file storage during tests."""
    d = tmp_path / "files"
    d.mkdir()
    return str(d)


@pytest.fixture()
def session_store(file_store_dir):
    """Return a TestSessionStore (no background thread)."""
    return TestSessionStore(
        ttl_hours=2,
        file_store_path=file_store_dir,
    )


@pytest.fixture()
def mock_config():
    """Return a mock Config object with sensible defaults."""
    cfg = MagicMock()
    cfg.MODEL = "claude-haiku-4-5"
    cfg.MAX_TOKENS = 8192
    cfg.SESSION_TTL_HOURS = 2
    cfg.FILE_TTL_HOURS = 24
    cfg.MEMORY_DIR = "./memory"
    cfg.DB_SERVER = "test-server"
    cfg.DB_NAME = "test-db"
    cfg.DB_USER = "test-user"
    cfg.DB_PASSWORD = "test-pass"
    cfg.HOST = "localhost"
    cfg.PORT = 8080
    cfg.FILE_STORE_PATH = ""
    cfg.db_connection_string = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=test-server;DATABASE=test-db;"
        "UID=test-user;PWD=test-pass;"
    )
    return cfg


@pytest.fixture()
def app(session_store, mock_config, file_store_dir):
    """
    Create a minimal Flask app with all blueprints registered and
    shared resources attached -- without calling the real create_app()
    (which tries to load Anthropic clients, context files, etc.).
    """
    static_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "server",
        "static",
    )

    flask_app = Flask(
        "test",
        static_folder=static_dir,
        static_url_path="/static",
    )
    flask_app.config["TESTING"] = True

    # Attach shared resources the same way create_app() does.
    flask_app.session_store = session_store
    flask_app.docs_text = "Test context docs"
    flask_app.docs_files = ["test_doc.md"]
    flask_app.config_obj = mock_config

    mock_config.FILE_STORE_PATH = file_store_dir

    flask_app.config["SESSION_STORE"] = session_store
    flask_app.config["CONTEXT_TEXT"] = "Test context docs"
    flask_app.config["CONTEXT_FILES"] = ["test_doc.md"]
    flask_app.config["ANTHROPIC_CLIENT"] = MagicMock()
    flask_app.config["FILE_STORE_PATH"] = file_store_dir
    flask_app.config["SEALINE_CONFIG"] = mock_config

    # Register blueprints
    from server.routes import sessions_bp, messages_bp, files_bp, health_bp, teams_bp
    from server.routes.health import init_health

    flask_app.register_blueprint(sessions_bp)
    flask_app.register_blueprint(messages_bp)
    flask_app.register_blueprint(files_bp)
    flask_app.register_blueprint(health_bp)
    flask_app.register_blueprint(teams_bp)

    init_health()

    return flask_app


@pytest.fixture()
def client(app):
    """Return a Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
#  Mock agent fixture — configures the fake SealineAgent class in
#  sys.modules["server.core.agent"] for each test that needs it.
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_agent():
    """
    Return a MagicMock that acts as a SealineAgent instance.

    Also patches sys.modules["server.core.agent"].SealineAgent so that
    ``from server.core.agent import SealineAgent`` returns a class whose
    constructor returns this mock instance.
    """
    agent_instance = MagicMock()
    agent_instance.send_message.return_value = iter([])
    agent_instance.send_message_sync.return_value = "Mock reply"
    agent_instance.messages = []
    agent_instance.total_input_tokens = 0
    agent_instance.total_output_tokens = 0
    agent_instance.cache_hits = 0
    agent_instance.sql_calls = 0
    agent_instance.generated_files = []

    mock_class = MagicMock(return_value=agent_instance)

    # Patch the SealineAgent in the fake module
    old_agent = sys.modules["server.core.agent"].SealineAgent
    sys.modules["server.core.agent"].SealineAgent = mock_class
    yield agent_instance, mock_class
    sys.modules["server.core.agent"].SealineAgent = old_agent


# ---------------------------------------------------------------------------
#  Helper fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def created_session(session_store):
    """Create a session in the store and return it."""
    return session_store.create()


@pytest.fixture()
def sample_file_record(file_store_dir):
    """Return a factory that creates a FileRecord and a real file on disk."""
    def _make(filename="report.xlsx", content=b"fake-content", file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        file_id = uuid.uuid4().hex[:12]
        file_path = os.path.join(file_store_dir, f"{file_id}_{filename}")
        with open(file_path, "wb") as f:
            f.write(content)
        return FileRecord(
            file_id=file_id,
            filename=filename,
            file_type=file_type,
            file_path=file_path,
            created_at=datetime.now(timezone.utc),
            size_bytes=len(content),
        )
    return _make
