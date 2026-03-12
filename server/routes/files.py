"""
File download endpoint.

Blueprint: files_bp
Prefix:    /api
"""

import logging
import mimetypes
import os

from flask import Blueprint, current_app, jsonify, send_file

logger = logging.getLogger(__name__)

files_bp = Blueprint("files", __name__, url_prefix="/api")


# Explicit MIME-type map for the file types the agent generates.
_MIME_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".html": "text/html",
    ".txt":  "text/plain",
    ".csv":  "text/csv",
}


def _error(message: str, code: str, status: int):
    """Return a standardised error JSON response."""
    return jsonify({"error": message, "code": code, "status": status}), status


def _find_file_record(file_id: str):
    """
    Search all sessions for a ``FileRecord`` whose ``file_id`` matches.

    Returns ``(FileRecord, Session)`` or ``(None, None)`` if not found.
    """
    store = current_app.session_store

    # list_sessions returns lightweight dicts; we need full Session objects,
    # so iterate through the underlying data.
    for summary in store.list_sessions():
        try:
            session = store.get(summary["session_id"])
        except KeyError:
            continue
        for fr in session.files:
            if fr.file_id == file_id:
                return fr, session

    return None, None


@files_bp.route("/files/<file_id>", methods=["GET"])
def download_file(file_id: str):
    """
    GET /api/files/<file_id>

    Serve a generated file (report, chart, etc.) for download or inline
    display.  Looks up the file record across all active sessions.
    """
    fr, session = _find_file_record(file_id)

    if fr is None:
        return _error(
            f"File '{file_id}' not found",
            "FILE_NOT_FOUND",
            404,
        )

    # Check that the file still exists on disk (it may have been cleaned up
    # by the TTL reaper).
    if not os.path.exists(fr.file_path):
        logger.warning(
            "File record exists but file on disk is missing: %s (%s)",
            fr.file_id,
            fr.file_path,
        )
        return _error(
            f"File '{file_id}' has expired and is no longer available",
            "FILE_NOT_FOUND",
            410,
        )

    # Determine the Content-Type.
    ext = os.path.splitext(fr.filename)[1].lower()
    mime_type = _MIME_TYPES.get(ext) or fr.file_type or "application/octet-stream"

    # Images are served inline so the frontend can display them directly in
    # the chat.  Everything else is served as an attachment (download).
    as_attachment = ext not in (".png", ".jpg", ".jpeg", ".html")

    logger.debug(
        "Serving file %s (%s) mime=%s attachment=%s",
        fr.file_id,
        fr.filename,
        mime_type,
        as_attachment,
    )

    return send_file(
        fr.file_path,
        mimetype=mime_type,
        as_attachment=as_attachment,
        download_name=fr.filename,
    )
