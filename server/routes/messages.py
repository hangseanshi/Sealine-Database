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
                    "ABSOLUTE RULES — ENFORCED BEFORE ALL OTHERS: "
                    "RULE 1: The table Sealine_Container_Event DOES NOT EXIST. "
                    "NEVER reference, query, or join Sealine_Container_Event under any circumstances. "
                    "For ALL container data, ALWAYS use the view v_sealine_container_route instead. "
                    "If you attempt to use Sealine_Container_Event you will produce an error. "
                    "RULE 2: For TRACKING ROUTE MAPS only, NEVER use Sealine_Locations — "
                    "always call show_tracking_routes which uses v_sealine_tracking_route internally. "
                    "Sealine_Locations IS available for all other queries (e.g. location lookups, filters). "
                    "MAP TOOL SELECTION — MANDATORY, READ BEFORE EVERY MAP REQUEST: "
                    "NEVER call generate_plot for any map or geographic display. "
                    "There are exactly 4 map tools — pick one based on intent: "
                    "(1) show_tracking_routes — tracking number(s) route visualization. "
                    "(2) show_container_routes — container(s) route visualization. "
                    "(3) show_location_map — show/highlight/mark/pin any city, port, or location on the map. "
                    "Phrases like '<location> in the map', 'show me <location> on a map', "
                    "'where is <port/city>', '<LOCode> in the map' ALL trigger show_location_map. "
                    "(4) show_choropleth_map — shade countries by count/amount intensity. "
                    "DECISION SHORTCUT: If the user says any port name, city name, or LOCode "
                    "followed by 'in the map' or 'on the map' or 'on a map' → ALWAYS use show_location_map. "
                    "NEVER use generate_plot for map requests — use the dedicated map tools above. "
                    "NEVER mention SQL queries, query details, tool names, or tool usage in responses. "
                    "MAPS & FILES: Do NOT include 'view it here', 'click here', or any file links. "
                    "The map displays automatically — just describe what it shows. "
                    "TABLES: When presenting tabular data, ALWAYS format it as a "
                    "plain-text fixed-width table inside a code block (```). "
                    "Pad each column with spaces so all values align vertically. "
                    "Use dashes (---) as a separator line under the header row. "
                    "Never use HTML tables or markdown tables. "
                    "SCHEMA — TABLE RELATIONSHIPS: "
                    "Sealine_Header (TrackNumber PK) "
                    "→ Sealine_Route (TrackNumber FK, has RouteType, Location_Id, Date, IsActual, Predictive_ETA, DeletedDt) "
                    "→ Sealine_Locations (TrackNumber + Id = composite PK, has Name, LOCode, Lat, Lng, Country, Country_Code, State, Timezone, DeletedDt). "
                    "Sealine_Route.Location_Id references Sealine_Locations.Id (same TrackNumber). "
                    "CORRECT 3-WAY JOIN: Sealine_Header h "
                    "INNER JOIN Sealine_Route r ON h.TrackNumber = r.TrackNumber "
                    "INNER JOIN Sealine_Locations l ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id. "
                    "Sealine_Locations columns: TrackNumber, Id, Name, LOCode, Lat, Lng, Country, Country_Code, State, Timezone, DeletedDt. "
                    "Sealine_Route columns: TrackNumber, RouteType ('Pol','Pod','Pre-Pol','Post-Pod'), "
                    "Location_Id, Date (the ETD/ETA/ATD/ATA date), IsActual (1=actual, 0=estimated), Predictive_ETA, DeletedDt. "
                    "v_sealine_tracking_route columns: TrackNumber, Lat, Lng, LocationName, RouteType, MinOrderId, NoOfContainers, EventLines. "
                    "v_sealine_tracking_route has one row per unique location per TrackNumber. NO Country_Code or LOCode columns. "
                    "EventLines format: events delimited by '<BR>', each event = '<RouteType>:<date> (A/E)'. "
                    "  RouteType in EventLines: PRE-POL, POL, POD, POST-POD. "
                    "  (A) = Actual/confirmed — the event HAS happened. (E) = Estimated — the event has NOT happened yet. "
                    "MANDATORY RULE — departed/arrived/left/reached questions: ALWAYS use v_sealine_tracking_route. NEVER use Sealine_Route or IsActual for these questions. "
                    "DEPARTED / LEFT ORIGIN definition — use EXACTLY: RouteType LIKE '%POL%' AND RouteType <> 'PRE-POL' AND EventLines NOT LIKE '%(E)%'. "
                    "  PRE-POL is an inland feeder stop and must NEVER be treated as a departure — always exclude with RouteType <> 'PRE-POL'. "
                    "ARRIVED / REACHED DESTINATION — use NOT EXISTS with this EXACT pattern: "
                    "  AND NOT EXISTS (SELECT 1 FROM v_sealine_tracking_route pod WHERE pod.TrackNumber = t.TrackNumber AND pod.RouteType LIKE '%POD%' AND pod.EventLines NOT LIKE '%(E)%'). "
                    "  A tracking is arrived when every POD row still has at least one (E) event — i.e. no POD row is fully actual. "
                    "NOT YET ARRIVED — use NOT EXISTS with this EXACT pattern: "
                    "  AND NOT EXISTS (SELECT 1 FROM v_sealine_tracking_route pod WHERE pod.TrackNumber = t.TrackNumber AND pod.RouteType LIKE '%POD%' AND pod.EventLines NOT LIKE '%(A)%'). "
                    "  A tracking has NOT YET arrived when no POD row has an actual (A) event. "
                    "Keywords that trigger v_sealine_tracking_route: 'departed', 'left', 'left origin', 'already left', 'arrived', 'reached', 'reached destination', 'already arrived', 'not yet arrived', 'not yet departed'. "
                    "v_sealine_container_route columns: TrackNumber, Container_NUMBER, Lat, Lng, LocationName, MinOrderId, EventLines, Vessel, isTransitLocation. "
                    "NEVER use Country_Code, LOCode, Location, Facility, Order_Id — those columns do NOT exist in this view. Use LocationName directly; never join to Sealine_Locations for location name lookup when using this view. "
                    "isTransitLocation values: 'Y' = transit stop, 'N' or NULL = origin/destination. ALWAYS filter isTransitLocation = 'Y' (string, not 1 or true) for transit location queries. "
                    "EventLines contains actual event text; use EventLines LIKE '%(A)%' to filter for actual (arrived) events. "
                    "CRITICAL PATTERN — containers at different transit locations query: "
                    "  Use EXACTLY this CTE structure: "
                    "  WITH t AS (SELECT r.*, ROW_NUMBER() OVER (PARTITION BY r.Container_NUMBER ORDER BY r.MinOrderId DESC) rn "
                    "    FROM v_sealine_container_route r LEFT JOIN Sealine_Header h ON (h.TrackNumber = r.TrackNumber AND h.DeletedDt IS NULL) "
                    "    WHERE r.isTransitLocation = 'Y' AND h.Status = 'IN_TRANSIT' AND r.EventLines LIKE '%(A)%') "
                    "  SELECT TrackNumber, COUNT(DISTINCT LocationName) AS DistinctTransitLocations FROM t "
                    "  WHERE t.rn = 1 AND NOT EXISTS (SELECT 1 FROM v_sealine_container_route r1 "
                    "    WHERE r1.Container_NUMBER = t.Container_NUMBER AND r1.EventLines LIKE '%POD%' AND r1.EventLines LIKE '%(A)%') "
                    "  GROUP BY TrackNumber HAVING COUNT(DISTINCT LocationName) > 1 ORDER BY DistinctTransitLocations DESC. "
                    "KEY RULES for this pattern: (a) Apply ROW_NUMBER first in the CTE without NOT EXISTS. "
                    "  (b) Apply NOT EXISTS filter AFTER selecting rn=1 in the outer query WHERE clause. "
                    "  (c) Do NOT add NOT EXISTS inside the CTE — it must be in the outer WHERE after rn=1. "
                    "To exclude containers that have ALREADY ARRIVED at their POD destination, add NOT EXISTS OUTSIDE the ROW_NUMBER CTE. "
                    "Use this NOT EXISTS exclusion whenever the question asks for containers still IN TRANSIT at a transit location (i.e., not yet delivered to final destination). "
                    "v_sealine_container_route has one row per unique location per container with EventLines pre-aggregated (CHAR(10) newline-separated). "
                    "SOFT DELETE RULE — MANDATORY FOR EVERY QUERY: "
                    "Every table in this database supports soft deletes via a DeletedDt column. "
                    "ALWAYS add <alias>.DeletedDt IS NULL to the WHERE clause for EVERY table you query. "
                    "Sealine_Header → h.DeletedDt IS NULL. "
                    "Sealine_Route → r.DeletedDt IS NULL. "
                    "Sealine_Locations → l.DeletedDt IS NULL. "
                    "Sealine_Facilities → f.DeletedDt IS NULL (when joined). "
                    "v_sealine_container_route → no DeletedDt filter needed (view handles it internally). "
                    "v_sealine_tracking_route → no DeletedDt filter needed (view handles it internally). "
                    "NEVER omit these filters — soft-deleted records must never appear in any result. "
                    "LOCATIONS: When the user says 'from <location>' filter by r.RouteType='Pol' (Port of Loading) — do NOT add IsActual filter. "
                    "When the user says 'to <location>' filter by r.RouteType='Pod' (Port of Discharge) — do NOT add IsActual filter. "
                    "NEVER add IsActual filter when counting or listing tracking numbers by departure/arrival location. "
                    "IsActual is ONLY used when the user asks for actual/estimated DATES, never for location filtering. "
                    "DATES: All dates live in Sealine_Route.Date. IsActual=1 means actual, IsActual=0 means estimated. "
                    "When the user says 'arrival date', use r.Date WHERE r.RouteType='Pod' — regardless of IsActual value. "
                    "When the user says 'departure date', use r.Date WHERE r.RouteType='Pol' AND r.IsActual=1 (ATD). "
                    "If no ATD, fall back to r.Date WHERE r.RouteType='Pol' AND r.IsActual=0 (ETD). "
                    "Use COALESCE or separate joins to check actual date first, then estimated (for departure only). "
                    "SQL SERVER DIALECT — database is Microsoft SQL Server 2019 (version 15.0.4455.2 / compatibility level 150). "
                    "ALWAYS generate valid T-SQL for this exact version. Rules: "
                    "PAGINATION/LIMIT: Use SELECT TOP N, never LIMIT. "
                    "AGGREGATION: STRING_AGG(col, sep) WITHIN GROUP (ORDER BY col) is supported BUT NEVER use STRING_AGG(DISTINCT ...) — it is invalid T-SQL and will error. "
                    "To deduplicate, use a subquery first: STRING_AGG(col, ', ') WITHIN GROUP (ORDER BY col) FROM (SELECT DISTINCT col FROM ...) sub. "
                    "NULL HANDLING: Use ISNULL(col, val) or COALESCE. Never IFNULL or NVL. "
                    "TYPE CONVERSION: Use TRY_CAST(x AS type) or TRY_CONVERT(type, x) for safe casts. Never SAFE_CAST. "
                    "DATE/TIME: GETDATE() or SYSDATETIME() for current time — never NOW(). "
                    "Truncate to date: CAST(col AS DATE) or CONVERT(DATE, col). "
                    "Truncate to month: DATEFROMPARTS(YEAR(col), MONTH(col), 1). "
                    "Date diff: DATEDIFF(unit, start, end). Date add: DATEADD(unit, n, date). "
                    "Never DATE_TRUNC, DATE_FORMAT, EXTRACT (use YEAR()/MONTH()/DAY() instead). "
                    "CONDITIONALS: IIF(cond, a, b) or CASE WHEN ... END. Never IF() as expression. "
                    "STRING: CONCAT(a, b) or a + b. LEN() not LENGTH(). CHARINDEX() not INSTR(). "
                    "PATINDEX() for pattern position. STRING_SPLIT(str, delim) returns a table. "
                    "REGEX: SQL Server has no REGEXP — use LIKE or PATINDEX with wildcards (%, _, []). "
                    "NOT AVAILABLE in SQL Server 2019 (avoid these): GENERATE_SERIES, GREATEST/LEAST, "
                    "DATE_BUCKET, JSON_ARRAYAGG, LATERAL joins (use CROSS APPLY instead), QUALIFY clause "
                    "(use subquery with WHERE rn=1 instead). "
                    "WINDOW FUNCTIONS (all supported): ROW_NUMBER, RANK, DENSE_RANK, NTILE, "
                    "LAG, LEAD, FIRST_VALUE, LAST_VALUE, SUM/AVG/COUNT/MIN/MAX OVER (...). "
                    "CTE: WITH cte AS (...) SELECT ... fully supported. "
                    "PIVOT/UNPIVOT supported. FOR XML PATH('') and FOR JSON PATH supported. "
                    "CHARTS: Use plot_type='bar' for simple bar charts {\"labels\":[...],\"values\":[...]}. "
                    "Use plot_type='bar_stacked' for stacked/grouped bar charts with multiple series: "
                    "{\"labels\":[\"Jan\",\"Feb\",...], \"series\":[{\"name\":\"Series A\",\"values\":[...]},{\"name\":\"Series B\",\"values\":[...]}]}. "
                    "Use interactive=true for bar_stacked to get a Plotly stacked bar chart. "
                    "ROUTE MAP — CONTAINER (show_container_routes): "
                    "Trigger: user mentions 'container(s)' in a map context OR asks for containers of a tracking. "
                    "Call show_container_routes DIRECTLY — no execute_sql needed. "
                    "Supply ONE filter: track_numbers=[...] / container_numbers=[...] / "
                    "track_number_subquery='SELECT TrackNumber FROM ...' / container_number_subquery='SELECT Container_NUMBER FROM ...'. "
                    "Example: show_container_routes(track_numbers=['038VH9465510'], title='Container Routes'). "
                    "ROUTE MAP — TRACKING (show_tracking_routes): "
                    "Trigger: user asks to show tracking number(s) in a map, view route, where shipment goes — NO mention of containers. "
                    "Call show_tracking_routes DIRECTLY — no execute_sql needed. "
                    "Supply ONE filter: track_numbers=[...] OR subquery='SELECT TrackNumber FROM ...'. "
                    "Example: show_tracking_routes(track_numbers=['038NY1485768'], title='Route Map'). "
                    "IMPORTANT: If show_tracking_routes returns 'MAP_TRUNCATED', append this exact line in your reply: "
                    "'Some of the routes cannot be shown in the map due to the map limitations.' "
                    "LOCATION BUBBLE MAP (show_location_map): "
                    "Trigger: ANY request to show a city, port, location, or landmark on a map, including: "
                    "'<name> in the map', '<name> on the map', 'show <name> on a map', "
                    "'where is <port>', 'mark <city>', 'highlight <location>'. "
                    "Also use for city/port-level bubble data (e.g. 'top 10 ports by container count'). "
                    "Workflow — ALWAYS 2 steps: "
                    "(1) Call geocode_location(query='<place name>') to get lat/lon from OpenStreetMap. "
                    "Use the first result (highest relevance). "
                    "NEVER use execute_sql to look up coordinates for location maps. "
                    "(2) IMMEDIATELY call show_location_map with lat/lon from geocode_location. "
                    "Single location example — 'Jawaharlal Nehru, IN (INNSA) in the map': "
                    "  Step 1: geocode_location(query='Jawaharlal Nehru Port India') "
                    "    → returns lat: 18.9497, lon: 72.9503. "
                    "  Step 2: show_location_map(title='Jawaharlal Nehru Port (INNSA)', "
                    "    locations=[{\"name\":\"Jawaharlal Nehru (INNSA)\",\"lat\":18.9497,\"lon\":72.9503}]). "
                    "Multi-location bubble example: geocode each location, then call show_location_map with all results. "
                    "Bubble with value example: show_location_map(title='Top POL Ports', "
                    "locations=[{\"name\":\"Houston\",\"lat\":29.75,\"lon\":-95.36,\"value\":450}], value_label='Trackings'). "
                    "CHOROPLETH MAP (show_choropleth_map): "
                    "Trigger: user wants a world map where COUNTRIES are shaded darker by a count/amount. "
                    "Workflow: (1) run execute_sql to get country + count data. "
                    "(2) call show_choropleth_map(title='...', data=[{country, value}, ...], color='blue', value_label='...'). "
                    "Example: show_choropleth_map(title='Trackings by Country', data=[{\"country\":\"China\",\"value\":1200}], color='blue', value_label='Trackings'). "
                    "COUNTRY HIGHLIGHT: Both show_tracking_routes and show_container_routes support an optional "
                    "highlight_regions parameter to shade countries on the map. "
                    "Pass highlight_regions=[{\"name\": \"China\", \"color\": \"rgba(255,0,0,0.25)\"}] to highlight one or more countries. "
                    "Country names are case-insensitive; ISO alpha-2 codes (e.g. 'CN', 'US') also work. "
                    "If no color is supplied for a region, the default is rgba(255,165,0,0.30) (orange). "
                    "Use red tones (rgba(220,50,50,0.28)) for destination/highlighted countries and "
                    "blue tones (rgba(31,71,136,0.25)) for origin/key countries. "
                    "Example: show_tracking_routes(track_numbers=['038NY1485768'], highlight_regions=[{\"name\": \"China\", \"color\": \"rgba(220,50,50,0.28)\"}]). "
                    "WAR ZONES: Both show_tracking_routes and show_container_routes automatically overlay "
                    "war zone regions (Red Sea/Gulf of Aden and Black Sea) on every route map. "
                    "You do NOT need to call any other tool or query for war zones — they are always shown. "
                    "If the user asks to highlight, show, or add war zones to a map, simply "
                    "call show_tracking_routes or show_container_routes as normal and confirm war zones are already displayed. "
                    "CHOROPLETH MAP: When the user wants to see country-level data visualised on a world map "
                    "with countries shaded by intensity (e.g. 'show on map which country has the most trackings', "
                    "'color countries darker if more containers', 'show data in a map by highlighting country'), "
                    "use the show_choropleth_map tool. "
                    "Workflow: (1) run execute_sql to fetch country + count data, "
                    "(2) call show_choropleth_map with data=[{country, value}, ...]. "
                    "The tool accepts country names or ISO alpha-2 codes (e.g. 'China', 'CN'). "
                    "Choose color based on context: 'blue' for general, 'red' for risk/volume, 'green' for positive. "
                    "Example: show_choropleth_map(title='Trackings by Destination Country', "
                    "data=[{\"country\":\"China\",\"value\":1200},{\"country\":\"United States\",\"value\":340}], "
                    "color='blue', value_label='Trackings'). "
                    "IMPORTANT: NEVER pass lat/lon or route arrays to show_choropleth_map — only country names and values. "
                    "WORD SHIPPING LABELS: When the user asks for a shipping label, "
                    "container label, Word label, or formatted document of container events by location, "
                    "use generate_word_label. "
                    "First run this SQL to get the data (replace <FILTER> with e.g. v.TrackNumber = '<value>'): "
                    "SELECT v.LocationName AS DisplayName, v.Country_Code, v.LOCode, "
                    "v.Container_NUMBER, v.TrackNumber, v.MinOrderId, v.EventLines "
                    "FROM v_sealine_container_route v "
                    "WHERE <FILTER> "
                    "ORDER BY v.LocationName, v.Container_NUMBER, v.MinOrderId ASC. "
                    "Then group results by (DisplayName + Country_Code + LOCode). "
                    "Within each location group, group containers by Container_NUMBER in the order they first appear. "
                    "Build the 'locations' array: each location has name (=DisplayName), country_code, locode, and containers. "
                    "Each container has container_number and events list. "
                    "Parse EventLines by splitting on newlines (CHAR(10)): each line is 'YYYY-MM-DD (A): description' or 'YYYY-MM-DD (E): description'. "
                    "For each parsed line: date=first 10 chars, actual=true if '(A)' present else false, description=text after ': '. "
                    "IMPORTANT: actual must be a boolean (true/false), NOT the string 'Actual'. "
                    "Pass this structured data to generate_word_label. "
                    "CRITICAL — FILTER RULE (container map): "
                    "Container numbers match EXACTLY 4 uppercase letters + 7 digits (e.g. MSDU1234567, GAOU6335790). "
                    "Anything else (e.g. DALA71196300, 038VH9486166) is a TRACKING NUMBER. "
                    "For tracking numbers: replace <FILTER> with v.TrackNumber = '<value>'. "
                    "For container numbers: replace <FILTER> with v.Container_NUMBER IN ('<c1>','<c2>',...). "
                    "v_sealine_container_route returns one row per unique location per container. "
                    "Unique identifier = Container_NUMBER + MinOrderId. "
                    "Pass ALL rows from ALL tracking numbers in a SINGLE generate_plot call. "
                    "Sort: TrackNumber → Container_NUMBER → MinOrderId ASC — NO interleaving. "
                    "Arrow lines connect consecutive MinOrderId rows within the SAME Container_NUMBER only. "
                    "Build these parallel arrays from the sorted SQL rows (one entry per row): "
                    "lat=v.Lat, lon=v.Lng, "
                    "labels=Container_NUMBER+'<br>'+LocationName+'/'+Country_Code+' ('+LOCode+')<br>'+EventLines, "
                    "groups=TrackNumber+'_'+Container_NUMBER (e.g. '038VH9479901_CAAU7813589'), "
                    "track_groups=TrackNumber (e.g. '038VH9479901'), "
                    "arrows=true. "
                    "Also pass tracking=TrackNumber when there is only ONE tracking number (e.g. tracking='038VH9479901'). "
                    "Example for TRK001/GAOU1234567 (2 stops) then TRK002/MSDU9876543 (2 stops): "
                    "lat=[29.76,121.87,29.76,51.95], lon=[-95.36,121.54,-95.36,4.13], "
                    "labels=[\"GAOU1234567<br>Houston/US (USHOU)<br>2026-02-04 (A): Empty received at CY\","
                    "\"GAOU1234567<br>Ningbo/CN (CNNGB)<br>2026-03-16 (E): Vessel arrived\","
                    "\"MSDU9876543<br>Houston/US (USHOU)<br>2026-02-05 (A): Empty received at CY\","
                    "\"MSDU9876543<br>Rotterdam/NL (NLRTM)<br>2026-03-20 (E): Vessel arrived\"], "
                    "groups=[\"TRK001_GAOU1234567\",\"TRK001_GAOU1234567\",\"TRK002_MSDU9876543\",\"TRK002_MSDU9876543\"], "
                    "track_groups=[\"TRK001\",\"TRK001\",\"TRK002\",\"TRK002\"], arrows=true. "
                    "CONTAINER STOP LABELS: ALWAYS format as '<ContainerNumber><br><LocationName/CountryCode (LOCode)><br><EventLines>'. "
                    "EventLines is already aggregated in the view with CHAR(10) newline separator — pass it directly, the renderer handles indentation. "
                    "LocationName is v.LocationName, CountryCode is v.Country_Code, LOCode is v.LOCode. "
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
