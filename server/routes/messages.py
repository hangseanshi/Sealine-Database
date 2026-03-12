"""
Message streaming endpoint (SSE).

Blueprint: messages_bp
Prefix:    /api

This is the most critical route in the application.  It accepts a user
message, instantiates a ``SealineAgent``, streams its response as
Server-Sent Events, and persists the updated conversation state back to the
session store once the stream completes.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request

logger = logging.getLogger(__name__)

messages_bp = Blueprint("messages", __name__, url_prefix="/api")


def _error(message: str, code: str, status: int):
    """Return a standardised error JSON response."""
    return jsonify({"error": message, "code": code, "status": status}), status


def _sse_line(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --------------------------------------------------------------------------- #
#  POST /api/sessions/<session_id>/messages
# --------------------------------------------------------------------------- #

@messages_bp.route(
    "/sessions/<session_id>/messages",
    methods=["POST"],
)
def send_message(session_id: str):
    """
    Send a user message and stream the agent's response as SSE.

    Request body (JSON):
        {"message": "How many containers are in transit?"}

    Response:
        Content-Type: text/event-stream
        A sequence of SSE events (message_start, text_delta, tool_start,
        tool_result, file_generated, plot_generated, error, message_end).
    """

    # ---------------------------------------------------------------------- #
    #  1. Parse & validate the request body
    # ---------------------------------------------------------------------- #
    body = request.get_json(silent=True)
    if body is None:
        return _error(
            "Request body must be valid JSON",
            "INVALID_REQUEST",
            400,
        )

    message = body.get("message", "").strip() if isinstance(body.get("message"), str) else ""
    if not message:
        return _error(
            "The 'message' field is required and must be a non-empty string",
            "INVALID_REQUEST",
            400,
        )

    # ---------------------------------------------------------------------- #
    #  2. Look up the session
    # ---------------------------------------------------------------------- #
    store = current_app.session_store

    try:
        session = store.get(session_id)
    except KeyError:
        return _error(
            f"Session '{session_id}' not found",
            "SESSION_NOT_FOUND",
            404,
        )

    # ---------------------------------------------------------------------- #
    #  3. Capture shared resources from the application context
    #     (they must be read *before* entering the generator because the
    #     application context may not be active when the generator runs
    #     under certain WSGI servers).
    # ---------------------------------------------------------------------- #
    cfg = current_app.config_obj
    docs_text = current_app.docs_text
    docs_files = current_app.docs_files

    # Determine whether the database tool is available.
    try:
        import pyodbc  # noqa: F401
        db_enabled = True
    except ImportError:
        db_enabled = False

    # Generate a unique message id for this exchange.
    message_id = f"msg_{uuid.uuid4().hex[:12]}"

    # ---------------------------------------------------------------------- #
    #  4. Build the SSE generator
    # ---------------------------------------------------------------------- #
    def generate():
        """
        Generator that yields SSE-formatted strings.

        Instantiates a SealineAgent, calls ``send_message`` (which itself
        returns a generator of event dicts), translates each event dict to
        an SSE text frame, and finally persists session state.
        """
        from server.core.agent import SealineAgent

        # --- message_start ------------------------------------------------ #
        yield _sse_line("message_start", {
            "message_id": message_id,
            "session_id": session_id,
        })

        try:
            agent = SealineAgent(
                model=cfg.MODEL,
                system_prompt=(
                    "You are Claude, a helpful AI assistant and data analyst "
                    "for the Sealine shipping database. You have been given "
                    "the database schema and reference documents as context."
                ),
                max_tokens=cfg.MAX_TOKENS,
                docs_text=docs_text,
                docs_files=docs_files,
                db_enabled=db_enabled,
                session_id=session_id,
                file_store_path=cfg.FILE_STORE_PATH,
                messages=list(session.messages),  # copy to avoid mutation issues
            )

            # The agent's send_message yields dicts like:
            # {"event": "text_delta", "data": {"delta": "..."}}
            # Filter out message_start/message_end from the agent since
            # we emit our own (avoids duplicate events for the client).
            for evt in agent.send_message(message):
                event_name = evt.get("event", "unknown")
                if event_name in ("message_start", "message_end"):
                    continue  # We handle these ourselves above/below
                event_data = evt.get("data", {})
                yield _sse_line(event_name, event_data)

            # -------------------------------------------------------------- #
            #  5. Persist updated state back to the session
            # -------------------------------------------------------------- #
            session.messages = agent.messages
            # Accumulate usage counters (agent starts from 0 each message,
            # so we add its counts to the session's running totals).
            session.total_input_tokens += agent.total_input_tokens
            session.total_output_tokens += agent.total_output_tokens
            session.cache_hits += agent.cache_hits
            session.sql_calls += agent.sql_calls
            session.last_active = datetime.now(timezone.utc)

            # Append any newly generated files to the session record.
            # Skip error dicts (which have an "error" key instead of "file_id").
            if hasattr(agent, "generated_files") and agent.generated_files:
                from server.sessions.store import FileRecord

                for fdict in agent.generated_files:
                    if "error" in fdict or "file_id" not in fdict:
                        continue  # Skip error returns from generators
                    fr = FileRecord(
                        file_id=fdict["file_id"],
                        filename=fdict["filename"],
                        file_type=fdict["file_type"],
                        file_path=fdict["file_path"],
                        created_at=datetime.now(timezone.utc),
                        size_bytes=fdict.get("size_bytes", 0),
                    )
                    session.files.append(fr)

            # --- message_end ---------------------------------------------- #
            yield _sse_line("message_end", {
                "message_id": message_id,
                "usage": {
                    "input_tokens": session.total_input_tokens,
                    "output_tokens": session.total_output_tokens,
                    "cache_read_tokens": session.cache_hits,
                    "sql_calls": session.sql_calls,
                },
            })

        except Exception as exc:
            # -------------------------------------------------------------- #
            #  Error handling — emit an SSE error event so the client knows
            #  something went wrong mid-stream.
            # -------------------------------------------------------------- #
            logger.exception(
                "Error during message processing for session %s",
                session_id,
            )

            error_code = "AGENT_ERROR"
            error_message = str(exc)

            # Attempt to classify the error for a more specific code.
            exc_type_name = type(exc).__name__
            if "AuthenticationError" in exc_type_name:
                error_code = "CLAUDE_API_ERROR"
                error_message = "Anthropic API authentication failed"
            elif "RateLimitError" in exc_type_name:
                error_code = "RATE_LIMITED"
                error_message = "Anthropic API rate limit reached. Please wait and try again."
            elif "APIConnectionError" in exc_type_name:
                error_code = "CLAUDE_API_ERROR"
                error_message = "Could not connect to Anthropic API"
            elif "APIStatusError" in exc_type_name:
                error_code = "CLAUDE_API_ERROR"
            elif "pyodbc" in exc_type_name.lower() or "sql" in error_message.lower():
                error_code = "DB_UNAVAILABLE"

            yield _sse_line("error", {
                "error": error_message,
                "code": error_code,
                "recoverable": False,
            })

            # Still emit message_end so the client knows the stream is done.
            yield _sse_line("message_end", {
                "message_id": message_id,
                "usage": {
                    "input_tokens": session.total_input_tokens,
                    "output_tokens": session.total_output_tokens,
                    "cache_read_tokens": session.cache_hits,
                    "sql_calls": session.sql_calls,
                },
            })

    # ---------------------------------------------------------------------- #
    #  6. Return a streaming response
    # ---------------------------------------------------------------------- #
    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",       # Disable nginx buffering
            "Connection": "keep-alive",
        },
    )
