"""
Health check endpoint.

Blueprint: health_bp
Prefix:    /api
"""

import logging
import time

from flask import Blueprint, current_app, jsonify

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__, url_prefix="/api")

# Captured once when the module is first loaded inside the running process.
# Re-set in the ``init_health`` helper so each app factory call gets its own
# baseline (important for testing).
_start_time: float = time.time()


def init_health() -> None:
    """Reset the process start timestamp.  Called from the app factory."""
    global _start_time
    _start_time = time.time()


def _check_db_connection() -> bool:
    """Return True if we can open a pyodbc connection to SQL Server."""
    try:
        import pyodbc

        cfg = current_app.config_obj
        conn = pyodbc.connect(cfg.db_connection_string, timeout=5)
        conn.close()
        return True
    except Exception as exc:
        logger.warning("DB health-check failed: %s", exc)
        return False


@health_bp.route("/health", methods=["GET"])
def health():
    """
    GET /api/health

    Returns server health information including database connectivity,
    uptime, model in use, and active session count.
    """
    cfg = current_app.config_obj
    store = current_app.session_store

    db_connected = _check_db_connection()
    uptime_seconds = round(time.time() - _start_time, 1)
    active_sessions = len(store.list_sessions())

    payload = {
        "status": "healthy",
        "version": "1.0.0",
        "model": cfg.MODEL,
        "db_connected": db_connected,
        "uptime_seconds": uptime_seconds,
        "active_sessions": active_sessions,
    }

    return jsonify(payload), 200
