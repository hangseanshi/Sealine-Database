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


def _select_skills(message: str, skill_texts: dict) -> str:
    """
    Classify the user message and select only relevant skill files.

    Returns a concatenated string of selected skill file contents, or empty
    string if no skills match (in which case only base docs are used).
    """
    if not skill_texts:
        return ""

    msg = message.lower()
    selected = []

    # SQL dialect — data/count/list queries
    sql_triggers = [
        "how many", "list", "count", "top ", "group by", "report",
        "number of", "total", "average", "sum", "which tracking",
        "between", "where", "select", "percentage", "breakdown"
    ]
    if any(t in msg for t in sql_triggers):
        selected.append(skill_texts.get("sql", ""))

    # Map tools — any map/route/location/visualisation
    map_triggers = [
        "map", "route", "location", "port", "show me", "where is",
        "in the map", "on the map", "shaded", "choropleth",
        "highlight", "war zone", "container route", "tracking route",
        "visuali"
    ]
    if any(t in msg for t in map_triggers):
        selected.append(skill_texts.get("map", ""))

    # Auto-detect — bare tracking/container number, or explicit mention
    words = msg.strip().split()
    is_bare_id = len(words) == 1 and (words[0][0].isdigit() or words[0][0].isalpha())
    detect_triggers = ["tracking", "container", "-"]
    if is_bare_id or any(t in msg for t in detect_triggers):
        selected.append(skill_texts.get("detect", ""))

    # Output format — always included (formatting rules apply to every response)
    selected.append(skill_texts.get("output", ""))

    # Filter out empty strings and join
    selected = [s for s in selected if s]
    return "\n\n---\n\n".join(selected) if selected else ""


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
    system_prompt_text = current_app.system_prompt_text
    base_docs_text = current_app.docs_text
    docs_files = current_app.docs_files
    skill_texts = current_app.skill_texts

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
            # Select only relevant skills for this message to reduce tokens
            skill_context = _select_skills(message, skill_texts)
            filtered_docs = base_docs_text
            if skill_context:
                filtered_docs += "\n\n---\n\n" + skill_context

            agent = SealineAgent(
                model=cfg.MODEL,
                system_prompt=system_prompt_text,
                max_tokens=cfg.MAX_TOKENS,
                docs_text=filtered_docs,
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

                # Enrich file/plot events with client-friendly fields.
                # The file_generator returns file_type/file_path which the
                # client doesn't need; the client needs type, download_url, url.
                if event_name in ("file_generated", "plot_generated"):
                    fid = event_data.get("file_id", "")
                    file_url = f"/api/files/{fid}" if fid else ""
                    event_data = {
                        **event_data,
                        "type": event_data.get("file_type", "application/octet-stream"),
                        "download_url": file_url,
                        "url": file_url,
                    }

                    # Register the FileRecord immediately so /api/files/<id>
                    # resolves while the stream is still open.  Without this,
                    # the browser receives the URL before the record exists in
                    # the session store and gets a 404.
                    if fid and event_data.get("file_path"):
                        from server.sessions.store import FileRecord
                        session.files.append(FileRecord(
                            file_id=fid,
                            filename=event_data.get("filename", ""),
                            file_type=event_data.get("file_type", ""),
                            file_path=event_data["file_path"],
                            created_at=datetime.now(timezone.utc),
                            size_bytes=event_data.get("size_bytes", 0),
                        ))

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
                error_code = "OPENAI_API_ERROR"
                error_message = "Azure OpenAI authentication failed. Check server configuration."
            elif "RateLimitError" in exc_type_name:
                error_code = "RATE_LIMITED"
                error_message = "Azure OpenAI rate limit reached. Please wait and try again."
            elif "APIConnectionError" in exc_type_name:
                error_code = "OPENAI_API_ERROR"
                error_message = "Could not connect to Azure OpenAI"
            elif "APIStatusError" in exc_type_name:
                error_code = "OPENAI_API_ERROR"
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
