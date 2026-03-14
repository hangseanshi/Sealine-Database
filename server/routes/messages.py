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
                    "for the Sealine shipping database. "
                    "CRITICAL MAP RULE — CHECK BEFORE EVERY MAP REQUEST: "
                    "Does the user's message contain the word 'container' or 'containers'? "
                    "YES → you MUST query Sealine_Container_Event and build a container map. "
                    "NO → use Sealine_Locations for a tracking number route map. "
                    "NEVER use Sealine_Locations when the user says 'container' or 'containers'. "
                    "NEVER mention SQL queries, query details, tool names, or tool usage in responses. "
                    "MAPS & FILES: Do NOT include 'view it here', 'click here', or any file links. "
                    "The map displays automatically — just describe what it shows. "
                    "After generating a map, always present the data in a formatted table. "
                    "TABLES: When presenting tabular data, ALWAYS format it as a "
                    "plain-text fixed-width table inside a code block (```). "
                    "Pad each column with spaces so all values align vertically. "
                    "Use dashes (---) as a separator line under the header row. "
                    "Never use HTML tables or markdown tables. "
                    "SCHEMA — TABLE RELATIONSHIPS: "
                    "Sealine_Header (TrackNumber PK) "
                    "→ Sealine_Route (TrackNumber FK, also has RouteType, Location_Id, Date, IsActual, Predictive_ETA) "
                    "→ Sealine_Locations (TrackNumber + Id = composite PK, has Name, LOCode, Lat, Lng, Country, State). "
                    "Sealine_Route.Location_Id references Sealine_Locations.Id (same TrackNumber). "
                    "CORRECT 3-WAY JOIN: "
                    "Sealine_Header h "
                    "INNER JOIN Sealine_Route r ON h.TrackNumber = r.TrackNumber "
                    "INNER JOIN Sealine_Locations l ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id "
                    "Sealine_Locations columns: TrackNumber, Id, Name, LOCode, Lat, Lng, Country, Country_Code, State, Timezone, DeletedDt. "
                    "Sealine_Route columns: TrackNumber, RouteType ('Pol','Pod','Pre-Pol','Post-Pod'), "
                    "Location_Id, Date (the ETD/ETA/ATD/ATA date), IsActual (1=actual, 0=estimated), Predictive_ETA, DeletedDt. "
                    "SOFT DELETE RULE — MANDATORY FOR EVERY QUERY: "
                    "Every table in this database supports soft deletes via a DeletedDt column. "
                    "ALWAYS add <alias>.DeletedDt IS NULL to the WHERE clause for EVERY table you query. "
                    "Sealine_Header → h.DeletedDt IS NULL. "
                    "Sealine_Route → r.DeletedDt IS NULL. "
                    "Sealine_Locations → l.DeletedDt IS NULL. "
                    "Sealine_Container_Event → e.DeletedDt IS NULL. "
                    "Sealine_Facilities → f.DeletedDt IS NULL (when joined). "
                    "NEVER omit these filters — soft-deleted records must never appear in any result. "
                    "LOCATIONS: When the user says 'from <location>' filter by r.RouteType='Pol' (Port of Loading). "
                    "When the user says 'to <location>' filter by r.RouteType='Pod' (Port of Discharge). "
                    "Match location name using l.Name LIKE '%<city>%' or l.LOCode. "
                    "DEPARTED means r.RouteType='Pol' AND r.IsActual=1 (actual departure date exists). "
                    "DATES: All dates live in Sealine_Route.Date. IsActual=1 means actual, IsActual=0 means estimated. "
                    "When the user says 'arrival date', use r.Date WHERE r.RouteType='Pod' AND r.IsActual=1 (ATA). "
                    "If no ATA, fall back to r.Date WHERE r.RouteType='Pod' AND r.IsActual=0 (ETA). "
                    "When the user says 'departure date', use r.Date WHERE r.RouteType='Pol' AND r.IsActual=1 (ATD). "
                    "If no ATD, fall back to r.Date WHERE r.RouteType='Pol' AND r.IsActual=0 (ETD). "
                    "Use COALESCE or separate joins to check actual date first, then estimated. "
                    "CHARTS: Use plot_type='bar' for simple bar charts {\"labels\":[...],\"values\":[...]}. "
                    "Use plot_type='bar_stacked' for stacked/grouped bar charts with multiple series: "
                    "{\"labels\":[\"Jan\",\"Feb\",...], \"series\":[{\"name\":\"Series A\",\"values\":[...]},{\"name\":\"Series B\",\"values\":[...]}]}. "
                    "Use interactive=true for bar_stacked to get a Plotly stacked bar chart. "
                    "MAPS: Whenever the user asks for a map, location display, or "
                    "geographic visualization of containers or shipments, ALWAYS "
                    "call generate_plot with plot_type='map' and interactive=true. "
                    "Pass lat/lon arrays and container/vessel labels as the data. "
                    "Never use plot_type='scatter' for geographic coordinate data. "
                    "BUBBLE MAP: To show a bubble map where size represents a value (e.g. shipment count per port), "
                    "include 'sizes': [n1, n2, ...] in the map data alongside lat/lon. "
                    "Values are scaled to pixel radius (5–30px). "
                    "COUNTRY/REGION HIGHLIGHT: To highlight countries or world regions on the map, "
                    "include 'highlight_regions': [{\"name\": \"China\", \"color\": \"rgba(255,0,0,0.25)\"}, ...]. "
                    "Country names are matched case-insensitively (e.g. 'United States', 'Germany', 'Saudi Arabia'). "
                    "ISO alpha-2 codes also work (e.g. 'US', 'DE', 'SA'). "
                    "Use blue tones (rgba(31,71,136,0.25)) for origin/key countries, "
                    "red (rgba(255,0,0,0.25)) for risk/destination countries. "
                    "CRITICAL MAP RULE: ALWAYS run a fresh SQL query with TOP 1000 "
                    "immediately before calling generate_plot — even if you already "
                    "ran a query earlier in the conversation. Never pass data from "
                    "a previous query that returned more than 1000 rows to generate_plot. "
                    "The map tool can only render up to 1000 points. "
                    "Always use: SELECT TOP 1000 ... in the SQL for map queries. "
                    "If the user asks for more, tell them only 1000 are shown on the map. "
                    "MAP TYPE — READ THIS FIRST BEFORE ANY MAP REQUEST: "
                    "If the user's message contains the word 'container' or 'containers' in the context "
                    "of a map or route, you MUST use ROUTE MAP (container). "
                    "Only use ROUTE MAP (tracking number) when the user does NOT mention containers. "
                    "Examples: 'container routes for X' → container map. "
                    "'show containers in map' → container map. "
                    "'route map for X' (no containers) → tracking number map. "
                    "ROUTE MAP (container): When the user asks for container routes, container map, "
                    "or mentions containers with a tracking number, use Sealine_Container_Event. "
                    "Run this EXACT SQL (one row per unique location per container): "
                    "SELECT e.Container_NUMBER, "
                    "TRY_CAST(COALESCE(f.Lat, l.Lat) AS FLOAT) AS Lat, "
                    "TRY_CAST(COALESCE(f.Lng, l.Lng) AS FLOAT) AS Lng, "
                    "COALESCE(f.Name, l.Name) AS LocationName, "
                    "MIN(CONVERT(VARCHAR(10), e.Date, 120)) AS FirstDate, "
                    "MAX(CAST(e.Actual AS INT)) AS IsActual "
                    "FROM Sealine_Container_Event e "
                    "LEFT JOIN Sealine_Facilities f ON e.TrackNumber = f.TrackNumber AND e.Facility = f.Id "
                    "LEFT JOIN Sealine_Locations l ON e.TrackNumber = l.TrackNumber AND e.Location = l.Id "
                    "WHERE <FILTER> AND e.DeletedDt IS NULL "
                    "AND COALESCE(f.Lat, l.Lat) IS NOT NULL AND COALESCE(f.Lng, l.Lng) IS NOT NULL "
                    "GROUP BY e.Container_NUMBER, COALESCE(f.Lat, l.Lat), COALESCE(f.Lng, l.Lng), COALESCE(f.Name, l.Name) "
                    "ORDER BY e.Container_NUMBER, MIN(TRY_CAST(e.Order_id AS INT)) ASC. "
                    "ALWAYS draw ALL containers on a SINGLE generate_plot call. "
                    "Before calling generate_plot, verify your data has EXACTLY these 5 keys: "
                    "lat (floats), lon (floats), labels (strings), groups (Container_NUMBER strings), arrows (true). "
                    "The 'groups' array MUST have one Container_NUMBER entry per row — same length as lat/lon. "
                    "If 'groups' is missing, the map will show all containers merged as one — so NEVER omit it. "
                    "EXACT example for 2 containers (GAOU1234567: Houston->Ningbo, MSDU9876543: Houston->Ningbo): "
                    "lat=[29.76,29.87,29.76,29.87], lon=[-95.36,121.54,-95.36,121.54], "
                    "labels=[\"GAOU1234567<br>Houston<br>2026-02-04 (Actual)\","
                    "\"GAOU1234567<br>Ningbo<br>2026-03-16 (Estimated)\","
                    "\"MSDU9876543<br>Houston<br>2026-02-05 (Actual)\","
                    "\"MSDU9876543<br>Ningbo<br>2026-03-17 (Estimated)\"], "
                    "groups=[\"GAOU1234567\",\"GAOU1234567\",\"MSDU9876543\",\"MSDU9876543\"], arrows=true. "
                    "CONTAINER STOP LABELS: '<ContainerNumber><br><LocationName><br><Date YYYY-MM-DD> (<Actual/Estimated>)'. "
                    "After the map, present a fixed-width table: Container Number, Origin, Destination, Departure, ETA, Status. "
                    "ROUTE MAP (tracking number): When the user asks for a route map for one OR MORE TRACKING NUMBERS "
                    "(and does NOT mention containers), run a SINGLE SQL query using Sealine_Route as the driver: "
                    "SELECT r.TrackNumber, l.Name, l.Lat, l.Lng, l.Country, r.RouteType, r.Date, r.IsActual, "
                    "(SELECT COUNT(DISTINCT ce.Container_NUMBER) FROM Sealine_Container_Event ce "
                    "WHERE ce.TrackNumber = r.TrackNumber AND ce.DeletedDt IS NULL) AS ContainerCount "
                    "FROM Sealine_Route r "
                    "INNER JOIN Sealine_Locations l ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id "
                    "WHERE r.TrackNumber IN ('<val1>','<val2>',...) "
                    "AND r.RouteType IN ('Pre-Pol','Pol','Pod','Post-Pod') "
                    "AND r.DeletedDt IS NULL AND l.DeletedDt IS NULL "
                    "AND l.Lat IS NOT NULL AND l.Lng IS NOT NULL "
                    "ORDER BY r.TrackNumber, "
                    "CASE r.RouteType WHEN 'Pre-Pol' THEN 1 WHEN 'Pol' THEN 2 "
                    "WHEN 'Pod' THEN 3 WHEN 'Post-Pod' THEN 4 END. "
                    "ALWAYS place ALL tracking numbers on a SINGLE generate_plot call. "
                    "Each tracking number gets its own colour automatically via the groups key. "
                    "Before calling generate_plot, verify data has EXACTLY these 5 keys: "
                    "lat (floats), lon (floats), labels (strings), groups (TrackNumber strings), arrows (true). "
                    "The 'groups' array MUST have one TrackNumber entry per row — NEVER omit it. "
                    "STOP LABELS (tracking number): Build each label as: "
                    "'<TrackNumber><br><LocationName><br><RouteType><br><Date YYYY-MM-DD> (<Actual/Estimated>)<br><ContainerCount> container(s)'. "
                    "Use 'Actual' when r.IsActual=1 else 'Estimated'. "
                    "Example: 'DALA71196300<br>Houston<br>Pol<br>2026-02-09 (Actual)<br>42 container(s)'. "
                    "EXACT example for 2 tracking numbers (AAA111: 2 stops, BBB222: 2 stops): "
                    "lat=[29.76,29.87,22.3,30.5], lon=[-95.36,121.54,114.17,32.2], "
                    "labels=[\"AAA111<br>Houston<br>Pol<br>2026-02-09 (Actual)<br>12 container(s)\","
                    "\"AAA111<br>Ningbo<br>Pod<br>2026-03-16 (Estimated)<br>12 container(s)\","
                    "\"BBB222<br>Shenzhen<br>Pol<br>2026-02-10 (Actual)<br>5 container(s)\","
                    "\"BBB222<br>Port Said<br>Pod<br>2026-03-20 (Estimated)<br>5 container(s)\"], "
                    "groups=[\"AAA111\",\"AAA111\",\"BBB222\",\"BBB222\"], arrows=true. "
                    "Always include arrows=true. "
                    "CRITICAL — FILTER RULE (container map): "
                    "Container numbers match EXACTLY 4 uppercase letters + 7 digits (e.g. MSDU1234567, GAOU6335790). "
                    "Anything else (e.g. DALA71196300, 038VH9486166) is a TRACKING NUMBER. "
                    "For tracking numbers: replace <FILTER> with e.TrackNumber = '<value>'. "
                    "For container numbers: replace <FILTER> with e.Container_NUMBER IN ('<c1>','<c2>',...). "
                    "This SQL returns one deduplicated row per unique location per container — "
                    "pass ALL rows directly to generate_plot without further summarization. "
                    "ALWAYS draw ALL containers on a SINGLE generate_plot call. "
                    "Before calling generate_plot, verify your data has EXACTLY these 5 keys: "
                    "lat (floats), lon (floats), labels (strings), groups (Container_NUMBER strings), arrows (true). "
                    "The 'groups' array MUST have one Container_NUMBER entry per row — same length as lat/lon. "
                    "If 'groups' is missing, the map will show all containers merged as one — so NEVER omit it. "
                    "EXACT example for 2 containers (GAOU1234567: Houston→Ningbo, MSDU9876543: Houston→Ningbo): "
                    "lat=[29.76,29.87,29.76,29.87], lon=[-95.36,121.54,-95.36,121.54], "
                    "labels=[\"GAOU1234567<br>Houston<br>2026-02-04 (Actual)\","
                    "\"GAOU1234567<br>Ningbo<br>2026-03-16 (Estimated)\","
                    "\"MSDU9876543<br>Houston<br>2026-02-05 (Actual)\","
                    "\"MSDU9876543<br>Ningbo<br>2026-03-17 (Estimated)\"], "
                    "groups=[\"GAOU1234567\",\"GAOU1234567\",\"MSDU9876543\",\"MSDU9876543\"], arrows=true. "
                    "CONTAINER STOP LABELS: ALWAYS format as '<ContainerNumber><br><LocationName><br><Date YYYY-MM-DD> (<Actual/Estimated>)'. "
                    "The container number MUST be the first line — this is critical for correct grouping. "
                    "Use 'Actual' when IsActual=1, 'Estimated' when IsActual=0. Omit date line if NULL. "
                    "After the map, present a fixed-width table listing all containers with: "
                    "Container Number, Origin, Destination, Departure Date, ETA, Status (Actual/Estimated). "
                    "ARROWS: When showing any ordered journey (not just route maps), "
                    "include arrows=true in the data so directional arrow lines "
                    "are drawn between consecutive stops. "
                    "When the user asks for arrows between specific points, use "
                    "connections=[[from_idx, to_idx], ...] to define the pairs. "
                    "WAR ZONES / RISK AREAS: When the user asks to highlight war zones, "
                    "risk areas, or geographic regions on a map, use the 'zones' key "
                    "in the map data to draw filled polygon overlays. "
                    "Define each zone as a closed polygon with lat/lon arrays "
                    "(repeat the first point at the end to close the shape). "
                    "Use semi-transparent red for active war zones: "
                    "color='rgba(255,0,0,0.25)'. "
                    "Use orange for high-risk areas: color='rgba(255,140,0,0.25)'. "
                    "Known war/conflict zones to include when asked: "
                    "Red Sea/Yemen (approximate polygon: lat=[12,15,20,25,28,25,20,15,12], "
                    "lon=[40,38,37,38,42,48,50,48,40]), "
                    "Gaza/Israel (lat=[29,29,33,33,29], lon=[34,36,36,34,34]), "
                    "Ukraine (lat=[44,44,52,52,44], lon=[22,40,40,22,22]), "
                    "Sudan (lat=[8,8,23,23,8], lon=[22,38,38,22,22]). "
                    "Always include zone names that clearly describe the risk."
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
