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
                    "the database schema and reference documents as context. "
                    "IMPORTANT: Never mention SQL queries, query details, tool "
                    "names, or tool usage in your responses. Do not include "
                    "phrases like 'SQL Query Used:', 'I ran the following query', "
                    "'Here is the query I used', or any similar descriptions of "
                    "internal tool calls. Present only the results and analysis. "
                    "TABLES: When presenting tabular data, ALWAYS format it as a "
                    "plain-text fixed-width table inside a code block (```). "
                    "Pad each column with spaces so all values align vertically. "
                    "Use dashes (---) as a separator line under the header row. "
                    "Never use HTML tables or markdown tables. "
                    "LOCATIONS: When the user says 'from <location>' always query the "
                    "POL (Port of Loading) in Sealine_Route (RouteType='Pol'). "
                    "When the user says 'to <location>' always query the "
                    "POD (Port of Discharge) in Sealine_Route (RouteType='Pod'). "
                    "DATES: When the user says 'arrival date' or 'arrive date', use "
                    "ATA first (RouteType='Pod', IsActual=1). If no ATA exists, "
                    "fall back to ETA (RouteType='Pod', IsActual=0). "
                    "When the user says 'departure date' or 'depart date', use "
                    "ATD first (RouteType='Pol', IsActual=1). If no ATD exists, "
                    "fall back to ETD (RouteType='Pol', IsActual=0). "
                    "Use COALESCE pattern: check actual date first, then estimated. "
                    "MAPS: Whenever the user asks for a map, location display, or "
                    "geographic visualization of containers or shipments, ALWAYS "
                    "call generate_plot with plot_type='map' and interactive=true. "
                    "Pass lat/lon arrays and container/vessel labels as the data. "
                    "Never use plot_type='scatter' for geographic coordinate data. "
                    "ROUTE MAP (tracking number): When the user asks for a route map "
                    "for a TRACKING NUMBER, ONLY use the Sealine_Route table joined to "
                    "Sealine_Locations. Query the 4 route stops in this fixed order: "
                    "Pre-Pol (RouteType='Pre-Pol'), Pol (RouteType='Pol'), "
                    "Pod (RouteType='Pod'), Post-Pod (RouteType='Post-Pod'). "
                    "Skip any stop that has no location or NULL lat/lng. "
                    "Pass the stops in that exact order to generate_plot so arrows "
                    "flow Pre-Pol → Pol → Pod → Post-Pod. "
                    "Always include arrows=true so directional arrow lines are drawn. "
                    "MERGED STOPS: Before building the map data, check for duplicate "
                    "locations: if Pre-Pol and Pol share the same lat/lng, merge them "
                    "into a single point labelled 'PRE-POL/POL (<location name>)' and "
                    "do NOT draw an arrow between them. "
                    "If Pod and Post-Pod share the same lat/lng, merge them into a "
                    "single point labelled 'POD/POST-POD (<location name>)' and do NOT "
                    "draw an arrow between them. "
                    "Only include each merged point once in the lat/lon arrays. "
                    "Label each non-merged point with its RouteType and location name. "
                    "ROUTE MAP (container): When the user asks for a route map for a "
                    "CONTAINER or CONTAINERS, use the Sealine_Container_Event table "
                    "joined to Sealine_Locations (via Location = Id, same TrackNumber) "
                    "and Sealine_Facilities (via Facility = Id, same TrackNumber). "
                    "Use COALESCE(f.Lat, l.Lat) and COALESCE(f.lng, l.Lng) for coordinates. "
                    "Filter to rows where the lat/lng are not NULL. "
                    "Draw ONE separate map per container (grouped by Container_NUMBER). "
                    "Within each container's map, order the points by "
                    "TRY_CAST(Order_id AS INT) ASC so arrows flow from lower to higher Order_id. "
                    "Always include arrows=true. "
                    "Label each point with its Order_id and Description. "
                    "ARROWS: When showing any ordered journey (not just route maps), "
                    "include arrows=true in the data so directional arrow lines "
                    "are drawn between consecutive stops. "
                    "When the user asks for arrows between specific points, use "
                    "connections=[[from_idx, to_idx], ...] to define the pairs."
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
