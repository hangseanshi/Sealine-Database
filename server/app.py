"""
Flask application factory for Sealine Data Chat API.

Creates the Flask app, registers route blueprints, initialises shared
resources (session store, context loader, Anthropic client), and sets up
static file serving for the React frontend.

Usage:
    # Development
    python -m flask --app server.app run --debug --port 8080

    # Production
    gunicorn -c server/gunicorn.conf.py 'server.app:create_app()'
"""

from __future__ import annotations

import logging
import os
import tempfile

from dotenv import load_dotenv

# Load .env before anything reads os.environ (Anthropic client, config, etc.)
# Use explicit path so it works regardless of cwd (e.g. preview servers).
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_project_root, ".env"))

import anthropic
import httpx
from flask import Flask, send_from_directory

from server.config import get_config
from server.core.context_loader import load_md_files
from server.sessions.store import SessionStore

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """
    Flask application factory.

    Initialises:
      - Configuration from environment variables
      - Session store (in-memory, with background cleanup)
      - Context loader (markdown files from memory/ directory)
      - Anthropic API client (with SSL bypass)
      - Route blueprints (sessions, messages, files, health, teams)
      - Static file serving for React frontend
    """
    cfg = get_config()

    # --- Create Flask app ---
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    app = Flask(
        __name__,
        static_folder=static_dir,
        static_url_path="/static",
    )

    # --- Logging ---
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # --- File store directory ---
    file_store_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "tmp", "files"
    )
    os.makedirs(file_store_path, exist_ok=True)

    # --- Session store ---
    session_store = SessionStore(
        ttl_hours=cfg.SESSION_TTL_HOURS,
        file_store_path=file_store_path,
        cleanup_interval_seconds=600,  # every 10 minutes
    )

    # --- Context loader (memory/*.md files) ---
    memory_dir = cfg.MEMORY_DIR
    if not os.path.isabs(memory_dir):
        # Resolve relative to project root (parent of server/)
        project_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        memory_dir = os.path.join(project_root, memory_dir)

    context_text, context_files = load_md_files(memory_dir)
    logger.info(
        "Loaded %d context file(s) from %s", len(context_files), memory_dir
    )

    # --- Anthropic client (shared, with SSL bypass) ---
    anthropic_client = anthropic.Anthropic(
        http_client=httpx.Client(verify=False),
    )

    # --- Store shared resources on app for access by routes ---
    # Flask config dict (standard pattern)
    app.config["SESSION_STORE"] = session_store
    app.config["CONTEXT_TEXT"] = context_text
    app.config["CONTEXT_FILES"] = context_files
    app.config["ANTHROPIC_CLIENT"] = anthropic_client
    app.config["FILE_STORE_PATH"] = file_store_path
    app.config["SEALINE_CONFIG"] = cfg

    # Direct attributes on app (routes access these via current_app.*)
    app.session_store = session_store
    app.docs_text = context_text
    app.docs_files = context_files
    app.config_obj = cfg
    cfg.FILE_STORE_PATH = file_store_path

    # --- Register blueprints ---
    # Import inside factory to avoid circular imports
    from server.routes import sessions_bp, messages_bp, files_bp, health_bp, teams_bp
    from server.routes.health import init_health

    app.register_blueprint(sessions_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(teams_bp)

    # Initialise health check baseline timestamp
    init_health()

    # --- Serve React frontend ---
    @app.route("/")
    def serve_index():
        """Serve the React app's index.html at the root."""
        index_path = os.path.join(static_dir, "index.html")
        if os.path.isfile(index_path):
            return send_from_directory(static_dir, "index.html")
        # If no React build exists yet, return a simple placeholder
        return (
            "<h1>Sealine Data Chat</h1>"
            "<p>React frontend not built yet. "
            "Place the build output in <code>server/static/</code>.</p>"
            "<p>API is available at <code>/api/health</code></p>"
        ), 200

    @app.route("/<path:path>")
    def serve_static_or_fallback(path: str):
        """
        Serve static files if they exist, otherwise fall back to index.html
        for client-side routing.  API routes are handled by blueprints
        (registered with url_prefix=/api) and take priority.
        """
        # Try to serve the file from static/
        file_path = os.path.join(static_dir, path)
        if os.path.isfile(file_path):
            return send_from_directory(static_dir, path)
        # Fall back to index.html for client-side routing
        index_path = os.path.join(static_dir, "index.html")
        if os.path.isfile(index_path):
            return send_from_directory(static_dir, "index.html")
        return "", 404

    logger.info(
        "Sealine Data Chat API initialised (model=%s, db=%s:%s, sessions_ttl=%dh)",
        cfg.MODEL,
        cfg.DB_SERVER,
        cfg.DB_NAME,
        cfg.SESSION_TTL_HOURS,
    )

    return app
