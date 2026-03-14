"""
Session management endpoints.

Blueprint: sessions_bp
Prefix:    /api
"""

import getpass
import logging
import os
import shutil
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

sessions_bp = Blueprint("sessions", __name__, url_prefix="/api")


def _error(message: str, code: str, status: int):
    """Return a standardised error JSON response."""
    return jsonify({"error": message, "code": code, "status": status}), status


# --------------------------------------------------------------------------- #
#  POST /api/sessions
# --------------------------------------------------------------------------- #

@sessions_bp.route("/sessions", methods=["POST"])
def create_session():
    """
    Create a new chat session.

    Returns JSON with session_id, created_at, model, and db_enabled.
    """
    store = current_app.session_store
    cfg = current_app.config_obj

    try:
        session = store.create()
    except Exception as exc:
        logger.exception("Failed to create session")
        return _error(
            f"Failed to create session: {exc}",
            "AGENT_ERROR",
            500,
        )

    # Determine whether the database is available for this session.
    try:
        import pyodbc  # noqa: F401
        db_enabled = True
    except ImportError:
        db_enabled = False

    # Resolve the OS login name; fall back gracefully if unavailable.
    try:
        username = getpass.getuser()
    except Exception:
        username = os.environ.get("USERNAME", os.environ.get("USER", ""))

    logger.info("Session created: %s (user=%s)", session.session_id, username)

    return jsonify({
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "model": cfg.MODEL,
        "db_enabled": db_enabled,
        "username": username,
    }), 201


# --------------------------------------------------------------------------- #
#  GET /api/sessions/<session_id>
# --------------------------------------------------------------------------- #

@sessions_bp.route("/sessions/<session_id>", methods=["GET"])
def get_session(session_id: str):
    """
    Return session metadata and usage stats.

    Does NOT include full message history (messages are internal to the
    agent and can be very large).
    """
    store = current_app.session_store

    try:
        session = store.get(session_id)
    except KeyError:
        return _error(
            f"Session '{session_id}' not found",
            "SESSION_NOT_FOUND",
            404,
        )

    # Count user turns (exclude tool-result pseudo-user messages).
    message_count = 0
    for msg in session.messages:
        if msg.get("role") == "user":
            content = msg.get("content")
            # tool_result entries are lists of dicts with type "tool_result"
            if isinstance(content, str):
                message_count += 1
            elif isinstance(content, list):
                if content and not (
                    isinstance(content[0], dict)
                    and content[0].get("type") == "tool_result"
                ):
                    message_count += 1

    files_generated = []
    for fr in session.files:
        files_generated.append({
            "file_id": fr.file_id,
            "filename": fr.filename,
            "type": fr.file_type,
            "created_at": fr.created_at.isoformat(),
        })

    return jsonify({
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "model": session.model,
        "message_count": message_count,
        "usage": {
            "input_tokens": session.total_input_tokens,
            "output_tokens": session.total_output_tokens,
            "cache_hits": session.cache_hits,
            "sql_calls": session.sql_calls,
        },
        "files_generated": files_generated,
    }), 200


# --------------------------------------------------------------------------- #
#  DELETE /api/sessions/<session_id>
# --------------------------------------------------------------------------- #

@sessions_bp.route("/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id: str):
    """
    Delete a session and clean up its generated files from disk.
    """
    store = current_app.session_store

    try:
        session = store.get(session_id)
    except KeyError:
        return _error(
            f"Session '{session_id}' not found",
            "SESSION_NOT_FOUND",
            404,
        )

    # Delete the session from the store (store.delete handles file cleanup).
    store.delete(session_id)

    logger.info("Session deleted: %s", session_id)

    return jsonify({
        "status": "deleted",
        "session_id": session_id,
    }), 200
