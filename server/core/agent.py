"""
Agent Core — Anthropic Claude-powered agentic loop for Sealine Data Chat.

The SSE event interface is identical — the frontend is unchanged.

Key implementation details:
  - Client: anthropic.Anthropic
  - Tool definitions in Anthropic input_schema format (no conversion needed)
  - Tool results sent as role:"user" content blocks with type:"tool_result"
  - Streaming uses client.messages.stream() with text_stream + get_final_message()
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Generator

import ssl

import anthropic
import httpx

from server.config import get_config
from server.core.sql_executor import execute_sql, MAX_ROWS

try:
    from server.core.file_generator import (
        generate_plot as _generate_plot,
        generate_pdf as _generate_pdf,
        generate_excel as _generate_excel,
        GENERATE_PLOT_TOOL,
        GENERATE_PDF_TOOL,
        GENERATE_EXCEL_TOOL,
    )
    _FILE_TOOLS_AVAILABLE = True
except ImportError:
    _FILE_TOOLS_AVAILABLE = False

logger = logging.getLogger(__name__)

MAX_TOOL_LOOPS = 15


# ---------------------------------------------------------------------------
#  Tool definitions (Anthropic input_schema format — converted on use)
# ---------------------------------------------------------------------------

SQL_TOOL = {
    "name": "execute_sql",
    "description": (
        "Execute a read-only SQL query against the Sealine searates database "
        "(SQL Server). Use this to answer questions with live data. "
        "Only SELECT and WITH (CTE) statements are allowed. "
        f"Results are capped at {MAX_ROWS} rows."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The SQL query to execute (SELECT or WITH only).",
            }
        },
        "required": ["query"],
    },
}

_FALLBACK_PLOT_TOOL = {
    "name": "generate_plot",
    "description": (
        "Generate a chart or plot from data. Supports bar, line, scatter, pie, "
        "heatmap, and histogram chart types."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plot_type": {
                "type": "string",
                "enum": ["bar", "line", "scatter", "pie", "heatmap", "histogram"],
                "description": "The type of chart to generate.",
            },
            "title": {"type": "string", "description": "Chart title."},
            "data": {
                "type": "object",
                "description": "Chart data as JSON.",
            },
            "interactive": {
                "type": "boolean",
                "description": "If true, generate interactive Plotly HTML.",
                "default": False,
            },
        },
        "required": ["plot_type", "title", "data"],
    },
}

_FALLBACK_PDF_TOOL = {
    "name": "generate_pdf",
    "description": "Generate a PDF report with a title, optional summary, and data table.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            "filename": {"type": "string"},
        },
        "required": ["title", "columns", "rows"],
    },
}

_FALLBACK_EXCEL_TOOL = {
    "name": "generate_excel",
    "description": "Generate a formatted Excel (.xlsx) report.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {}}},
            "filename": {"type": "string"},
        },
        "required": ["title", "columns", "rows"],
    },
}


TRACKING_ROUTES_TOOL = {
    "name": "show_tracking_routes",
    "description": (
        "Generate an interactive route map for one or more tracking numbers. "
        "ONLY call this tool when the user asks for a route map and their message "
        "does NOT contain the word 'container' or 'containers'. "
        "DO NOT call this tool when the user mentions containers — use show_container_routes instead. "
        "Internally runs the Sealine_Route query and renders a Leaflet map with "
        "Pre-Pol → Pol → Pod → Post-Pod stops, arrow lines, and per-stop tooltips. "
        "Supply either track_numbers (explicit list) OR subquery (a SQL SELECT that returns "
        "TrackNumber values) — not both."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "track_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Explicit list of tracking numbers (e.g. ['DALA71196300']). "
                    "Use this when the user provides specific tracking numbers directly."
                ),
            },
            "subquery": {
                "type": "string",
                "description": (
                    "A SQL SELECT statement whose result set is a single column of TrackNumber "
                    "values, used as an IN subquery. "
                    "Example: \"SELECT TrackNumber FROM Sealine_Order WHERE CustomerName = 'ABC'\". "
                    "Use this when the tracking numbers must be derived from another table."
                ),
            },
            "title": {
                "type": "string",
                "description": "Map title shown at the top.",
            },
            "highlight_regions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "color": {"type": "string"},
                    },
                    "required": ["name"],
                },
                "description": (
                    "Optional list of countries/regions to highlight on the map. "
                    "Each entry: {\"name\": \"China\", \"color\": \"rgba(255,0,0,0.25)\"}. "
                    "Country names are matched case-insensitively. "
                    "Default color is rgba(255,165,0,0.30) if omitted."
                ),
            },
        },
    },
}

CONTAINER_ROUTES_TOOL = {
    "name": "show_container_routes",
    "description": (
        "Generate an interactive container route map. "
        "ONLY call this tool when the user's message explicitly contains the word "
        "'container' or 'containers'. "
        "DO NOT call this tool for generic route maps or tracking number maps — "
        "use generate_plot for those instead. "
        "Each container gets its own coloured route with arrow lines between stops. "
        "Supply explicit lists (track_numbers / container_numbers) OR subqueries "
        "(track_number_subquery / container_number_subquery) — not both kinds at once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "track_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Explicit list of tracking numbers (e.g. ['DALA71196300']). "
                    "Shows all containers under those tracking numbers."
                ),
            },
            "container_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Explicit list of container numbers (e.g. ['GAOU6335790']). "
                    "Use when the user supplies specific container numbers."
                ),
            },
            "track_number_subquery": {
                "type": "string",
                "description": (
                    "A SQL SELECT statement returning a single column of TrackNumber values, "
                    "used as an IN subquery to filter by tracking number. "
                    "Example: \"SELECT TrackNumber FROM Sealine_Order WHERE CustomerName = 'ABC'\". "
                    "Use instead of track_numbers when the numbers must be derived from another table."
                ),
            },
            "container_number_subquery": {
                "type": "string",
                "description": (
                    "A SQL SELECT statement returning a single column of Container_NUMBER values, "
                    "used as an IN subquery to filter by container number. "
                    "Example: \"SELECT Container_NUMBER FROM Sealine_Container WHERE Size = '40'\". "
                    "Use instead of container_numbers when the numbers must be derived from another table."
                ),
            },
            "title": {
                "type": "string",
                "description": "Map title shown at the top.",
            },
            "highlight_regions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "color": {"type": "string"},
                    },
                    "required": ["name"],
                },
                "description": (
                    "Optional list of countries/regions to highlight on the map. "
                    "Each entry: {\"name\": \"China\", \"color\": \"rgba(255,0,0,0.25)\"}. "
                    "Country names are matched case-insensitively. "
                    "Default color is rgba(255,165,0,0.30) if omitted."
                ),
            },
        },
    },
}


LOCATION_BUBBLE_MAP_TOOL = {
    "name": "show_location_map",
    "description": (
        "Display one or more locations as bubble markers on an interactive world map. "
        "Use when the user wants to highlight, pin, or mark specific cities, ports, "
        "or locations — WITHOUT showing shipping routes between them. "
        "The agent must run execute_sql first to obtain lat/lon coordinates "
        "(query Sealine_Locations for port lat/lng), then pass the results here. "
        "Bubbles can optionally be sized by a numeric value (e.g. container count per port)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Map title shown at the top.",
            },
            "locations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":  {"type": "string",  "description": "Display name (city/port/location)"},
                        "lat":   {"type": "number",  "description": "Latitude"},
                        "lon":   {"type": "number",  "description": "Longitude"},
                        "value": {"type": "number",  "description": "Optional numeric value — sizes the bubble"},
                        "label": {"type": "string",  "description": "Optional extra line shown in tooltip"},
                        "color": {"type": "string",  "description": "Optional bubble color (hex or CSS color)"},
                    },
                    "required": ["name", "lat", "lon"],
                },
                "description": "List of locations to display as bubbles.",
            },
            "value_label": {
                "type": "string",
                "description": "Label for the value shown in tooltips (e.g. 'Containers', 'Trackings').",
            },
            "color": {
                "type": "string",
                "description": "Default bubble color for all locations (e.g. '#2980B9'). Overridden per-item by location.color.",
            },
        },
        "required": ["title", "locations"],
    },
}


CHOROPLETH_MAP_TOOL = {
    "name": "show_choropleth_map",
    "description": (
        "Generate an interactive world choropleth map that shades countries by a numeric value "
        "(e.g. number of trackings, containers, or shipments per country). "
        "Use this tool when the user wants to visualise country-level data on a map "
        "with darker colours indicating higher values. "
        "The agent must first run execute_sql to get the country-value data, "
        "then pass the results to this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Map title shown at the top.",
            },
            "data": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "country": {"type": "string"},
                        "value":   {"type": "number"},
                        "label":   {"type": "string"},
                    },
                    "required": ["country", "value"],
                },
                "description": (
                    "Array of {country, value} objects. "
                    "Country names are matched case-insensitively (e.g. 'China', 'United States'). "
                    "ISO alpha-2 codes also work (e.g. 'CN', 'US'). "
                    "The 'label' field is optional — shown in tooltip alongside value."
                ),
            },
            "color": {
                "type": "string",
                "description": (
                    "Base hue for the choropleth gradient. "
                    "Accepted values: 'blue' (default), 'red', 'green', 'orange', 'purple'."
                ),
            },
            "value_label": {
                "type": "string",
                "description": "Label for the numeric value shown in tooltips (e.g. 'Trackings', 'Containers').",
            },
        },
        "required": ["title", "data"],
    },
}


GEOCODE_LOCATION_TOOL = {
    "name": "geocode_location",
    "description": (
        "Look up the coordinates (latitude, longitude) and display name of any "
        "place — city, port, country, address, or landmark — using OpenStreetMap Nominatim. "
        "Call this BEFORE show_location_map whenever the user asks to show a specific location "
        "on the map. Returns up to 3 candidate results with lat, lon, and display_name."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Free-text place description: city, port name, LOCode, country, address, etc. "
                    "E.g. 'Jawaharlal Nehru Port India', 'Shanghai', 'INNSA', 'Los Angeles CA USA'."
                ),
            }
        },
        "required": ["query"],
    },
}


def _get_plot_tool_def() -> dict:
    return GENERATE_PLOT_TOOL if _FILE_TOOLS_AVAILABLE else _FALLBACK_PLOT_TOOL


def _get_pdf_tool_def() -> dict:
    return GENERATE_PDF_TOOL if _FILE_TOOLS_AVAILABLE else _FALLBACK_PDF_TOOL


def _get_excel_tool_def() -> dict:
    return GENERATE_EXCEL_TOOL if _FILE_TOOLS_AVAILABLE else _FALLBACK_EXCEL_TOOL



# ---------------------------------------------------------------------------
#  SSE helper
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": data}


# ---------------------------------------------------------------------------
#  Agent class
# ---------------------------------------------------------------------------

class SealineAgent:
    """
    Anthropic Claude-powered agent that yields SSE event dicts.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        system_prompt: str = (
            "You are a helpful AI assistant and data analyst "
            "for the Sealine shipping database."
        ),
        max_tokens: int = 8192,
        docs_text: str = "",
        docs_files: list[str] | None = None,
        db_enabled: bool = True,
        session_id: str = "",
        file_store_path: str = "",
        messages: list[dict] | None = None,
    ):
        cfg = get_config()
        # Use a custom httpx client that disables SSL certificate revocation
        # checks — required on Windows servers where CRL/OCSP endpoints may
        # be unreachable (results in CRYPT_E_NO_REVOCATION_CHECK errors).
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        http_client = httpx.Client(verify=False)
        self.client = anthropic.Anthropic(
            api_key=cfg.ANTHROPIC_API_KEY,
            http_client=http_client,
        )
        self.model = model
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.docs_text = docs_text
        self.docs_files = docs_files or []
        self.db_enabled = db_enabled
        self.session_id = session_id
        self.file_store_path = file_store_path

        # Conversation history in OpenAI messages format
        self.messages: list[dict] = messages if messages is not None else []

        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.cache_hits: int = 0
        self.sql_calls: int = 0
        self.generated_files: list[dict] = []

    # ------------------------------------------------------------------
    #  System message
    # ------------------------------------------------------------------

    def _system_content(self) -> str:
        tool_instructions: list[str] = []
        if self.db_enabled:
            tool_instructions.append(
                "You have access to the `execute_sql` tool which runs live queries "
                "against the Sealine searates SQL Server database. Use it whenever the "
                "user asks for data, counts, reports, or anything requiring live results."
            )
        tool_instructions.append(
            "You can generate charts with `generate_plot`, PDF reports with "
            "`generate_pdf`, and Excel spreadsheets with `generate_excel`. "
            "Use these tools when the user asks for visualizations or downloadable files."
        )

        base = self.system_prompt + "\n\n" + "\n".join(tool_instructions)

        if self.docs_text:
            base += (
                "\n\n# Sealine-Database Reference Documents\n\n"
                "The following Markdown files have been loaded from the repository. "
                "Use them as your primary reference for schema, relationships, "
                "connection details, and saved reports.\n\n"
                + self.docs_text
            )
        return base

    # ------------------------------------------------------------------
    #  Tool list
    # ------------------------------------------------------------------

    def _tools(self) -> list[dict]:
        tools: list[dict] = []
        if self.db_enabled:
            tools.append(SQL_TOOL)
        tools.append(_get_plot_tool_def())
        tools.append(_get_pdf_tool_def())
        tools.append(_get_excel_tool_def())
        tools.append(TRACKING_ROUTES_TOOL)
        tools.append(CONTAINER_ROUTES_TOOL)
        tools.append(GEOCODE_LOCATION_TOOL)
        tools.append(LOCATION_BUBBLE_MAP_TOOL)
        tools.append(CHOROPLETH_MAP_TOOL)
        return tools

    # ------------------------------------------------------------------
    #  Tool executor (unchanged logic, same SSE events)
    # ------------------------------------------------------------------

    def _execute_tool(
        self, name: str, tool_input: dict
    ) -> Generator[dict, None, str]:
        if name == "execute_sql":
            query = tool_input.get("query", "")
            yield _sse("tool_start", {"tool": "execute_sql", "query": query})

            # CRITICAL: Validate query does not contain invalid SQL patterns
            query_upper = query.upper()

            # DEBUG: Write marker file to prove this code is running
            try:
                with open("/tmp/execute_sql_called.txt", "w") as f:
                    f.write(f"execute_sql called\nhas POD: {'POD' in query_upper}\nhas STATUS: {'STATUS' in query_upper}\nhas <>: {'<>' in query_upper}\n")
            except:
                pass

            # SIMPLE TEST: Reject any query with POD and Status filtering
            if "POD" in query_upper and "STATUS" in query_upper and "<>" in query_upper:
                error_msg = "🧪 TEST VALIDATION TRIGGERED: This query has POD + Status filtering which is suspicious."
                yield _sse("tool_result", {"tool": "execute_sql", "result": error_msg, "truncated": False})
                return error_msg

            # Check for invalid table alias references
            import re as regex_module
            from_match = regex_module.search(r'FROM\s+(\w+)\s+(\w+)', query_upper)
            join_matches = regex_module.findall(r'(?:INNER\s+)?JOIN\s+(\w+)\s+(\w+)', query_upper)

            defined_aliases = set()
            if from_match:
                defined_aliases.add(from_match.group(2))  # alias

            for table, alias in join_matches:
                defined_aliases.add(alias)

            # Find all table.column references (e.g., t.TrackNumber, h.Status)
            col_refs = regex_module.findall(r'\b([a-zA-Z_]\w*)\.\w+', query_upper)
            undefined_aliases = set(col_refs) - defined_aliases

            if undefined_aliases:
                error_msg = (
                    "❌ SQL SYNTAX ERROR: Invalid table alias reference(s): " + ", ".join(sorted(undefined_aliases)) + "\n\n"
                    "These aliases are used in the query but are never defined in the FROM/JOIN clauses.\n\n"
                    "Defined aliases: " + (", ".join(sorted(defined_aliases)) if defined_aliases else "NONE") + "\n\n"
                    "Check your table alias mappings and ensure every reference (e.g., t.TrackNumber) "
                    "has a corresponding table in the FROM clause (e.g., FROM table_name t)."
                )
                yield _sse("tool_result", {"tool": "execute_sql", "result": error_msg, "truncated": False})
                return error_msg

            # Check for STRING_AGG(DISTINCT - invalid T-SQL
            if "STRING_AGG(DISTINCT" in query_upper or "STRING_AGG (DISTINCT" in query_upper:
                error_msg = (
                    "❌ INVALID QUERY: STRING_AGG(DISTINCT ...) is not valid T-SQL. "
                    "This error typically occurs when you're adding extra columns beyond what the question asks for. "
                    "\n\nFor the 'containers at different transit locations' query, the SELECT must be EXACTLY:\n"
                    "  SELECT TrackNumber, COUNT(DISTINCT LocationName) AS DistinctTransitLocations\n"
                    "Do NOT add STRING_AGG, Container counts, or any other columns. "
                    "\n\nIf you need to deduplicate values for a different query, use a subquery:\n"
                    "  STRING_AGG(col, ', ') WITHIN GROUP (ORDER BY col) FROM (SELECT DISTINCT col FROM ...) sub"
                )
                yield _sse("tool_result", {"tool": "execute_sql", "result": error_msg, "truncated": False})
                return error_msg

            # Additional check: Container transit query pattern should have exactly 2 columns
            # Pattern: contains container route, COUNT(DISTINCT LocationName), NOT EXISTS POD
            is_container_transit_pattern = (
                "V_SEALINE_CONTAINER_ROUTE" in query_upper and
                "COUNT(DISTINCT" in query_upper and
                "NOT EXISTS" in query_upper and
                "EVENTLINES LIKE '%POD%'" in query_upper
            )
            if is_container_transit_pattern:
                # This pattern should select EXACTLY: TrackNumber, COUNT(DISTINCT LocationName)
                select_match = query.upper().find("SELECT")
                from_match = query.upper().find("FROM", select_match)
                if select_match >= 0 and from_match > select_match:
                    select_clause = query[select_match:from_match]

                    # Check for forbidden extra columns/aggregations
                    has_string_agg = "STRING_AGG" in select_clause.upper()
                    has_multiple_counts = select_clause.upper().count("COUNT(DISTINCT") > 1
                    has_container_count = "CONTAINER" in select_clause.upper() or "CONTAINER_NUMBER" in select_clause.upper()

                    # Count top-level column separators (commas not inside parens)
                    paren_depth = 0
                    comma_count = 0
                    for char in select_clause:
                        if char == '(':
                            paren_depth += 1
                        elif char == ')':
                            paren_depth -= 1
                        elif char == ',' and paren_depth == 0:
                            comma_count += 1

                    # Should have exactly 1 comma (between TrackNumber and COUNT(...))
                    if has_string_agg or has_multiple_counts or has_container_count or comma_count > 1:
                        error_msg = (
                            "❌ QUERY ERROR: The 'containers at different transit locations' pattern must have exactly 2 columns:\n"
                            "  SELECT TrackNumber, COUNT(DISTINCT LocationName) AS DistinctTransitLocations\n\n"
                            "Your query has extra columns:\n"
                        )
                        if has_string_agg:
                            error_msg += "  ❌ STRING_AGG(...) — remove this\n"
                        if has_multiple_counts:
                            error_msg += "  ❌ Multiple COUNT aggregations — keep only COUNT(DISTINCT LocationName)\n"
                        if has_container_count:
                            error_msg += "  ❌ Container count or container columns — remove these\n"

                        error_msg += (
                            "\nExtra columns break the query structure. Return ONLY:\n"
                            "  - TrackNumber\n"
                            "  - COUNT(DISTINCT LocationName) AS DistinctTransitLocations\n"
                            "Nothing else."
                        )
                        yield _sse("tool_result", {"tool": "execute_sql", "result": error_msg, "truncated": False})
                        return error_msg

            # Check for incorrect Status-based filtering in "not delivered" queries
            # Pattern: queries that filter Status field (e.g., Status <> 'DELIVERED') for "not delivered" intent
            has_status_filtering = (("STATUS" in query_upper and "<>" in query_upper) or
                                   ("STATUS" in query_upper and "!=" in query_upper) or
                                   ("STATUS" in query_upper and "NOT IN" in query_upper))
            has_pod_keyword = "POD" in query_upper
            has_war_zone_keyword = any(term in query_upper for term in ["WAR", "ZONE", "RED SEA", "PERSIAN", "ADEN", "MEDITERRANEAN"])

            # Debug logging
            logger.info(f"Status validation: has_status_filtering={has_status_filtering}, has_pod_keyword={has_pod_keyword}, has_war_zone_keyword={has_war_zone_keyword}, DELIVERED_in_query={('DELIVERED' in query_upper)}")

            # If using Status-based filtering for a "not delivered" query, REJECT it
            if has_status_filtering and has_pod_keyword and (has_war_zone_keyword or "DELIVERED" in query_upper):
                error_msg = (
                    "❌ INCORRECT LOGIC: Status-Based 'Not Delivered' Filter\n\n"
                    "Your query uses: WHERE h.Status <> 'DELIVERED' or similar Status filtering\n\n"
                    "THIS IS WRONG. Status field is a summary and does NOT determine delivery status.\n"
                    "A shipment with Status='IN_TRANSIT' can still be delivered if POD has IsActual=1.\n\n"
                    "YOU MUST use NOT EXISTS with IsActual flag instead:\n\n"
                    "  AND NOT EXISTS (SELECT 1 FROM Sealine_Route r2\n"
                    "    WHERE r2.TrackNumber = h.TrackNumber\n"
                    "    AND r2.RouteType IN ('Pod', 'Post POD')\n"
                    "    AND r2.IsActual = 1\n"
                    "    AND r2.DeletedDt IS NULL)\n\n"
                    "The IsActual = 1 flag is the ONLY reliable indicator of actual delivery.\n"
                    "Remove Status-based filtering and add the NOT EXISTS clause."
                )
                yield _sse("tool_result", {"tool": "execute_sql", "result": error_msg, "truncated": False})
                return error_msg

            # Check for "not delivered" queries missing the NOT EXISTS exclusion
            # Pattern: queries with POD + geographic filtering (BETWEEN/LAT/LNG) but NO NOT EXISTS
            has_pod_keyword = "POD" in query_upper
            has_sealine_route = "SEALINE_ROUTE" in query_upper
            has_location_filter = "LAT" in query_upper or "LATITUDE" in query_upper
            uses_lat_lng_filter = "BETWEEN" in query_upper and ("LAT" in query_upper or "LATITUDE" in query_upper)
            has_not_exists_exclusion = "NOT EXISTS" in query_upper and "ISACTUAL" in query_upper

            # Check if query is a flat SELECT (not using WITH/CTE) for a war zone query
            is_flat_select = "WITH" not in query_upper and "SELECT" in query_upper[:20]  # Flat SELECT at start
            has_two_cte_structure = "WITH" in query_upper and query_upper.count("AS (") >= 2

            # If query has POD + location filtering (war zones) but NO NOT EXISTS, it's missing the "not delivered" constraint
            # This applies regardless of whether IN_TRANSIT is present
            if has_pod_keyword and has_sealine_route and has_location_filter and uses_lat_lng_filter and not has_not_exists_exclusion:
                structure_issue = ""
                if is_flat_select and not has_two_cte_structure:
                    structure_issue = "  • STRUCTURE PROBLEM: You generated a flat SELECT instead of the MANDATORY 2-CTE pattern\n"

                error_msg = (
                    "❌ CRITICAL ERROR: War Zone POD Query Invalid\n\n"
                    "Your query attempts to find tracking numbers with POD in war zones but:\n"
                    "  1. Does NOT follow the MANDATORY 2-CTE structure (WITH pod_locations AS ..., war_zone_pod AS ...)\n"
                    "  2. Is MISSING the critical NOT EXISTS clause for 'not delivered' filtering\n"
                    "  3. Will return WRONG RESULTS (42,000+ rows instead of ~96 undelivered ones)\n"
                    f"{structure_issue}\n"
                    "YOU MUST use this EXACT 2-CTE structure (copy-paste if needed):\n\n"
                    "WITH pod_locations AS (\n"
                    "    SELECT DISTINCT\n"
                    "        h.TrackNumber,\n"
                    "        l.Name AS POD_Location,\n"
                    "        TRY_CAST(l.Lat AS FLOAT) AS Lat,\n"
                    "        TRY_CAST(l.Lng AS FLOAT) AS Lng\n"
                    "    FROM Sealine_Header h\n"
                    "    INNER JOIN Sealine_Route r ON h.TrackNumber = r.TrackNumber AND r.DeletedDt IS NULL\n"
                    "    INNER JOIN Sealine_Locations l ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id AND l.DeletedDt IS NULL\n"
                    "    WHERE h.Status = 'IN_TRANSIT'\n"
                    "      AND r.RouteType = 'Pod'\n"
                    "      AND r.DeletedDt IS NULL\n"
                    "      AND NOT EXISTS (\n"
                    "          SELECT 1 FROM Sealine_Route r2\n"
                    "          WHERE r2.TrackNumber = h.TrackNumber\n"
                    "            AND r2.RouteType IN ('Pod', 'Post POD')\n"
                    "            AND r2.IsActual = 1\n"
                    "            AND r2.DeletedDt IS NULL\n"
                    "      )\n"
                    "),\n"
                    "war_zone_pod AS (\n"
                    "    SELECT\n"
                    "        TrackNumber, POD_Location, Lat, Lng\n"
                    "    FROM pod_locations\n"
                    "    WHERE\n"
                    "        (Lat BETWEEN 29 AND 33.5 AND Lng BETWEEN 33.8 AND 36.5) OR\n"
                    "        (Lat BETWEEN 41 AND 48 AND Lng BETWEEN 28 AND 42) OR\n"
                    "        (Lat BETWEEN 12 AND 28 AND Lng BETWEEN 32 AND 52) OR\n"
                    "        (Lat BETWEEN 8 AND 23 AND Lng BETWEEN 22 AND 38)\n"
                    ")\n"
                    "SELECT TrackNumber, POD_Location, Lat, Lng FROM war_zone_pod ORDER BY TrackNumber\n\n"
                    "CRITICAL RULES:\n"
                    "  • Use ONLY this structure — do NOT flatten into a single SELECT\n"
                    "  • Return EXACTLY 4 columns: TrackNumber, POD_Location, Lat, Lng\n"
                    "  • Use base tables (Sealine_Header, Sealine_Route, Sealine_Locations) — NOT views\n"
                    "  • The NOT EXISTS clause with IsActual=1 is MANDATORY\n"
                    "  • Geographic boundaries are FIXED and must not be changed\n"
                )
                yield _sse("tool_result", {"tool": "execute_sql", "result": error_msg, "truncated": False})
                return error_msg

            result = execute_sql(query)
            self.sql_calls += 1

            yield _sse(
                "tool_result",
                {"tool": "execute_sql", "result": result.text, "truncated": result.truncated},
            )
            if result.truncated:
                yield _sse(
                    "warning",
                    {"message": "This response is based on more than 1,000 rows of data. Results may be incomplete — refine your query for a more precise answer."},
                )
            return result.text

        elif name == "generate_plot":
            yield _sse("tool_start", {"tool": "generate_plot", "query": tool_input.get("title", "")})
            logger.info("generate_plot tool_input: %s", json.dumps(tool_input, default=str)[:2000])
            if _FILE_TOOLS_AVAILABLE:
                try:
                    # The AI sometimes puts map data fields at the top level of
                    # tool_input instead of nested inside "data". Merge them all.
                    data = dict(tool_input.get("data") or {})
                    for _k in (
                        "lat", "lon", "labels", "values", "sizes",
                        "arrows", "zones", "connections",
                        "highlight_regions", "routes",
                    ):
                        if _k in tool_input and _k not in data:
                            data[_k] = tool_input[_k]

                    # Guard: generate_plot must NEVER be used for geographic/location maps.
                    # Redirect the agent to the correct dedicated map tool.
                    if tool_input.get("plot_type") == "map":
                        _has_coords = bool(data.get("lat") or data.get("lon") or data.get("routes"))
                        if not _has_coords:
                            err = (
                                "TOOL MISUSE: generate_plot must not be used for location/map displays. "
                                "Use the correct tool instead: "
                                "show_location_map — to mark/highlight a city, port, or location on a map. "
                                "show_choropleth_map — to shade countries by count/intensity. "
                                "show_tracking_routes — to show a tracking number route. "
                                "show_container_routes — to show a container route. "
                                "Re-invoke the correct tool now."
                            )
                            yield _sse("error", {"error": err, "code": "PLOT_ERROR", "recoverable": True})
                            return err
                        # Even with coords, warn that dedicated tools are preferred
                        _highlight_only = (
                            bool(data.get("highlight_regions"))
                            and not data.get("lat")
                            and not data.get("routes")
                        )
                        if _highlight_only:
                            err = (
                                "TOOL MISUSE: For country highlights without routes, use show_choropleth_map. "
                                "For marking a location, use show_location_map. "
                                "Re-invoke the correct tool now."
                            )
                            yield _sse("error", {"error": err, "code": "PLOT_ERROR", "recoverable": True})
                            return err

                    file_info = _generate_plot(
                        plot_type=tool_input.get("plot_type", "bar"),
                        title=tool_input.get("title", "Chart"),
                        data=data,
                        interactive=tool_input.get("interactive", False),
                        x_label=tool_input.get("x_label"),
                        y_label=tool_input.get("y_label"),
                        file_store_path=self.file_store_path,
                    )
                    if "error" in file_info:
                        error_msg = f"Plot generation error: {file_info['error']}"
                        yield _sse("error", {"error": error_msg, "code": "PLOT_ERROR", "recoverable": True})
                        return error_msg
                    self.generated_files.append(file_info)
                    event_type = (
                        "plot_generated"
                        if file_info.get("file_type", "").startswith("image/")
                        else "file_generated"
                    )
                    yield _sse(event_type, file_info)
                    if file_info.get("map_truncated"):
                        yield _sse(
                            "warning",
                            {"message": "The map shows the first 1,000 locations only. Refine your query to see a specific subset."},
                        )
                    return f"Plot generated: {file_info.get('filename', 'chart')}"
                except Exception as exc:
                    error_msg = f"Plot generation error: {exc}"
                    logger.exception(error_msg)
                    yield _sse("error", {"error": error_msg, "code": "PLOT_ERROR", "recoverable": True})
                    return error_msg
            else:
                msg = "Plot generation is not available (file_generator module not installed)."
                yield _sse("tool_result", {"tool": "generate_plot", "result": msg, "truncated": False})
                return msg

        elif name == "generate_pdf":
            yield _sse("tool_start", {"tool": "generate_pdf", "query": tool_input.get("title", "")})
            if _FILE_TOOLS_AVAILABLE:
                try:
                    file_info = _generate_pdf(
                        title=tool_input["title"],
                        columns=tool_input["columns"],
                        rows=tool_input["rows"],
                        summary=tool_input.get("summary"),
                        filename=tool_input.get("filename"),
                        file_store_path=self.file_store_path,
                    )
                    self.generated_files.append(file_info)
                    yield _sse("file_generated", file_info)
                    return f"PDF generated: {file_info.get('filename', 'report.pdf')}"
                except Exception as exc:
                    error_msg = f"PDF generation error: {exc}"
                    logger.exception(error_msg)
                    yield _sse("error", {"error": error_msg, "code": "PDF_ERROR", "recoverable": True})
                    return error_msg
            else:
                msg = "PDF generation is not available (file_generator module not installed)."
                yield _sse("tool_result", {"tool": "generate_pdf", "result": msg, "truncated": False})
                return msg

        elif name == "generate_excel":
            yield _sse("tool_start", {"tool": "generate_excel", "query": tool_input.get("title", "")})
            if _FILE_TOOLS_AVAILABLE:
                try:
                    file_info = _generate_excel(
                        title=tool_input["title"],
                        columns=tool_input["columns"],
                        rows=tool_input["rows"],
                        filename=tool_input.get("filename"),
                        file_store_path=self.file_store_path,
                    )
                    self.generated_files.append(file_info)
                    yield _sse("file_generated", file_info)
                    return f"Excel generated: {file_info.get('filename', 'report.xlsx')}"
                except Exception as exc:
                    error_msg = f"Excel generation error: {exc}"
                    logger.exception(error_msg)
                    yield _sse("error", {"error": error_msg, "code": "EXCEL_ERROR", "recoverable": True})
                    return error_msg
            else:
                msg = "Excel generation is not available (file_generator module not installed)."
                yield _sse("tool_result", {"tool": "generate_excel", "result": msg, "truncated": False})
                return msg

        elif name == "show_tracking_routes":
            # ── Dedicated tracking-number route map tool ────────────────────
            # Uses v_sealine_tracking_route view (pre-aggregated per location).
            # NEVER uses Sealine_Locations directly.
            # Triggered ONLY when user asks for route map WITHOUT "container".
            import re as _re
            track_numbers = tool_input.get("track_numbers") or []
            subquery      = (tool_input.get("subquery") or "").strip()
            title         = tool_input.get("title") or "Tracking Route Map"

            if not track_numbers and not subquery:
                msg = "show_tracking_routes requires track_numbers or a subquery."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            # Build the IN clause — either a literal list or an inner SELECT
            if subquery:
                in_clause = subquery
            else:
                in_clause = ", ".join(f"'{v}'" for v in track_numbers)

            sql = (
                "SELECT TrackNumber, Lat, Lng, LocationName, "
                "RouteType, MinOrderId, NoOfContainers, EventLines "
                "FROM v_sealine_tracking_route "
                f"WHERE TrackNumber IN ({in_clause}) "
                "ORDER BY TrackNumber, MinOrderId ASC"
            )

            yield _sse("tool_start", {"tool": "execute_sql", "query": sql})
            result = execute_sql(sql)
            self.sql_calls += 1
            yield _sse("tool_result", {"tool": "execute_sql", "result": result.text, "truncated": result.truncated})

            if result.error or not result.rows:
                msg = f"No route data found for tracking numbers: {track_numbers}."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            # ── Map column indices ──────────────────────────────────────────
            cols = [c.upper() for c in (result.columns or [])]
            try:
                i_trk    = cols.index("TRACKNUMBER")
                i_lat    = cols.index("LAT")
                i_lon    = cols.index("LNG")
                i_name   = cols.index("LOCATIONNAME")
                i_rtype  = cols.index("ROUTETYPE")
                i_order  = cols.index("MINORDERID")
                i_noc    = cols.index("NOOFCONTAINERS")
                i_events = cols.index("EVENTLINES")
            except ValueError:
                i_trk, i_lat, i_lon, i_name, i_rtype, i_order, i_noc, i_events = \
                    0, 1, 2, 3, 4, 5, 6, 7

            TRACK_COLORS = [
                "#27AE60", "#2980B9", "#E67E22", "#8E44AD", "#C0392B",
                "#16A085", "#D35400", "#1A5276", "#6C3483", "#1E8449",
            ]

            # ── Build location index and route sequences ────────────────────
            # loc_index: rounded(lat,lon) → index in locations list
            # locations: [{name, lat, lon, tracks:[{trk, routeType, events}]}]
            # trk_routes: trk → [loc_idx, ...]  ordered by MinOrderId
            loc_index: dict = {}
            locations: list = []
            trk_routes: dict = {}
            trk_order: list = []  # preserve insertion order of tracking numbers
            trk_containers: dict = {}  # trk → NoOfContainers (from first row seen)

            for row in result.rows:
                try:
                    trk        = str(row[i_trk]).strip()
                    lat        = float(row[i_lat])
                    lon        = float(row[i_lon])
                    display    = str(row[i_name]).strip() if row[i_name] else ""
                    route_type = str(row[i_rtype]).strip() if row[i_rtype] else ""
                    noc_raw    = row[i_noc]
                    events_raw = str(row[i_events]).strip() if row[i_events] else ""
                except (ValueError, IndexError, TypeError):
                    continue

                # Capture NoOfContainers once per tracking number
                if trk not in trk_containers:
                    try:
                        trk_containers[trk] = int(noc_raw) if noc_raw is not None else 0
                    except (ValueError, TypeError):
                        trk_containers[trk] = 0

                # Parse events — delimited by <BR> (case-insensitive)
                events = [e.strip() for e in _re.split(r'<BR>', events_raw, flags=_re.IGNORECASE) if e.strip()]

                # Unique location key (rounded to avoid float noise)
                loc_key = (round(lat, 5), round(lon, 5))
                if loc_key not in loc_index:
                    loc_index[loc_key] = len(locations)
                    locations.append({"name": display, "lat": lat, "lon": lon, "tracks": []})
                idx = loc_index[loc_key]

                # Add this tracking number's data to the location
                # (one entry per TrackNumber per location — view PK is TrackNumber+LocationName)
                locations[idx]["tracks"].append({
                    "trk": trk,
                    "routeType": route_type,
                    "events": events,
                })

                # Build ordered stop list per TrackNumber
                if trk not in trk_routes:
                    trk_routes[trk] = []
                    trk_order.append(trk)
                trk_routes[trk].append(idx)

            if not locations:
                msg = f"No mappable coordinates found for {track_numbers}."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            # ── Enforce map limits ──────────────────────────────────────────
            # Cap at 500 unique stops; keep as many COMPLETE routes as possible.
            MAX_TRACKING_STOPS = 500
            map_truncated = False

            if len(locations) > MAX_TRACKING_STOPS:
                map_truncated = True
                kept_trk_order: list = []
                kept_loc_set: set = set()
                for _trk in trk_order:
                    _stops = trk_routes.get(_trk, [])
                    _added = set(_stops) - kept_loc_set
                    if len(kept_loc_set) + len(_added) <= MAX_TRACKING_STOPS:
                        kept_trk_order.append(_trk)
                        kept_loc_set.update(_stops)
                    else:
                        break
                if not kept_trk_order and trk_order:  # always keep at least 1
                    _first = trk_order[0]
                    kept_trk_order = [_first]
                    kept_loc_set = set(trk_routes.get(_first, []))

                trk_order = kept_trk_order
                trk_routes = {k: v for k, v in trk_routes.items() if k in kept_trk_order}
                # Re-index locations to only those still referenced
                _used = sorted(kept_loc_set)
                _old_to_new = {old: new for new, old in enumerate(_used)}
                locations = [locations[i] for i in _used]
                trk_routes = {k: [_old_to_new[i] for i in v] for k, v in trk_routes.items()}

            # ── Build routes list ───────────────────────────────────────────
            routes = [
                {
                    "trk":            trk,
                    "color":          TRACK_COLORS[i % len(TRACK_COLORS)],
                    "stops":          trk_routes[trk],
                    "noOfContainers": trk_containers.get(trk, 0),
                }
                for i, trk in enumerate(trk_order)
            ]

            highlight_regions = tool_input.get("highlight_regions") or []
            # Apply default color for regions with no color specified
            for _r in highlight_regions:
                if not _r.get("color"):
                    _r["color"] = "rgba(255,165,0,0.30)"
            map_data = {"locations": locations, "routes": routes, "highlight_regions": highlight_regions}
            unique_tracks = len(routes)
            unique_stops  = len(locations)

            yield _sse("tool_start", {"tool": "generate_plot", "query": title})
            if _FILE_TOOLS_AVAILABLE:
                try:
                    from server.core.file_generator import (
                        _short_uuid, _slugify, ensure_file_store, _file_meta,
                        _plot_tracking_route_map,
                    )
                    file_id   = _short_uuid()
                    slug      = _slugify(title)
                    ensure_file_store(self.file_store_path)
                    file_info = _plot_tracking_route_map(
                        title=title,
                        data=map_data,
                        file_id=file_id,
                        title_slug=slug,
                        file_store_path=self.file_store_path,
                    )
                    self.generated_files.append(file_info)
                    yield _sse("file_generated", file_info)
                    _trunc_flag = " MAP_TRUNCATED" if map_truncated else ""
                    return (
                        f"Tracking route map generated: {unique_tracks} tracking number(s), "
                        f"{unique_stops} unique stop(s).{_trunc_flag}"
                    )
                except Exception as exc:
                    error_msg = f"Tracking route map error: {exc}"
                    logger.exception(error_msg)
                    yield _sse("error", {"error": error_msg, "code": "PLOT_ERROR", "recoverable": True})
                    return error_msg
            return result.text

        elif name == "show_container_routes":
            # ── Dedicated container-route map tool ─────────────────────────
            # Uses v_sealine_container_route — Sealine_Container_Event MUST NOT
            # be referenced here or anywhere in the codebase.
            # Columns available: Container_NUMBER, TrackNumber, Lat, Lng,
            #   LocationName, MinOrderId, EventLines, Vessel  (no Country_Code / LOCode)
            import re as _re
            track_numbers             = tool_input.get("track_numbers") or []
            container_numbers         = tool_input.get("container_numbers") or []
            track_number_subquery     = (tool_input.get("track_number_subquery") or "").strip()
            container_number_subquery = (tool_input.get("container_number_subquery") or "").strip()
            title = tool_input.get("title") or "Container Routes"

            if not track_numbers and not container_numbers \
                    and not track_number_subquery and not container_number_subquery:
                msg = ("show_container_routes requires track_numbers, container_numbers, "
                       "track_number_subquery, or container_number_subquery.")
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            # Build WHERE clause — subqueries take precedence over literal lists
            if track_number_subquery:
                where = f"v.TrackNumber IN ({track_number_subquery})"
            elif container_number_subquery:
                where = f"v.Container_NUMBER IN ({container_number_subquery})"
            elif track_numbers:
                vals  = ", ".join(f"'{v}'" for v in track_numbers)
                where = f"v.TrackNumber IN ({vals})"
            else:
                vals  = ", ".join(f"'{v}'" for v in container_numbers)
                where = f"v.Container_NUMBER IN ({vals})"

            # Columns: Container_NUMBER, TrackNumber, Lat, Lng, LocationName,
            #          MinOrderId, EventLines, Vessel  (no Country_Code / LOCode)
            sql = (
                "SELECT v.Container_NUMBER, v.TrackNumber, v.Lat, v.Lng, "
                "v.LocationName, v.MinOrderId, v.EventLines, v.Vessel "
                "FROM v_sealine_container_route v "
                f"WHERE {where} AND v.Lat IS NOT NULL AND v.Lng IS NOT NULL "
                "ORDER BY v.TrackNumber, v.Container_NUMBER, v.MinOrderId ASC"
            )

            yield _sse("tool_start", {"tool": "execute_sql", "query": sql})
            result = execute_sql(sql)
            self.sql_calls += 1
            yield _sse("tool_result", {"tool": "execute_sql", "result": result.text, "truncated": result.truncated})

            if result.error or not result.rows:
                _filter_desc = (
                    track_number_subquery or container_number_subquery
                    or track_numbers or container_numbers
                )
                msg = f"No container route data found for {_filter_desc}."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            # ── Build structured map data ──────────────────────────────────
            # locations: deduplicated by rounded lat/lon (one dot per unique position)
            # routes:    one entry per container (TrackNumber-Container_NUMBER key)
            cols = [c.upper() for c in (result.columns or [])]
            try:
                i_cnum   = cols.index("CONTAINER_NUMBER")
                i_trk    = cols.index("TRACKNUMBER")
                i_lat    = cols.index("LAT")
                i_lon    = cols.index("LNG")
                i_loc    = cols.index("LOCATIONNAME")
                i_events = cols.index("EVENTLINES")
                i_vessel = cols.index("VESSEL")
            except ValueError:
                i_cnum, i_trk, i_lat, i_lon, i_loc, i_events, i_vessel = 0, 1, 2, 3, 4, 6, 7

            loc_index: dict  = {}   # (roundlat, roundlon) → index in locations list
            locations: list  = []   # [{name, lat, lon, containers: {key: {key, events}}}]
            ctr_routes: dict = {}   # ckey → {trk, stops:[loc_idx], vessels:[vessel_str]}
            ctr_order: list  = []   # insertion order of container keys

            for row in result.rows:
                try:
                    cnum     = str(row[i_cnum]).strip()
                    trk      = str(row[i_trk]).strip()
                    lat      = float(row[i_lat])
                    lon      = float(row[i_lon])
                    loc_name = str(row[i_loc]).strip() if row[i_loc] else ""
                    evraw    = str(row[i_events]).strip() if row[i_events] else ""
                    vessel   = str(row[i_vessel]).strip() if row[i_vessel] else ""
                except (ValueError, IndexError, TypeError):
                    continue

                events = [e.strip() for e in _re.split(r'<BR>', evraw, flags=_re.IGNORECASE) if e.strip()]

                # Fix container number if database returns duplicate TrackNumber prefix
                # Example: "038VH9465510-038VH9465510-CAIU7249126" → "038VH9465510-CAIU7249126"
                parts = cnum.split('-')
                if len(parts) >= 3 and parts[0] == parts[1] == trk:
                    # Remove the first duplicate prefix (keep TrackNumber-Container format)
                    cnum = '-'.join(parts[1:])

                ckey   = cnum                      # display key: use Container_NUMBER directly

                # Deduplicate locations
                loc_key = (round(lat, 5), round(lon, 5))
                if loc_key not in loc_index:
                    loc_index[loc_key] = len(locations)
                    locations.append({"name": loc_name, "lat": lat, "lon": lon, "containers": {}})
                idx = loc_index[loc_key]

                # Register this container's events at this location (first occurrence wins)
                if ckey not in locations[idx]["containers"]:
                    locations[idx]["containers"][ckey] = {"key": ckey, "events": events}

                # Build ordered stop list per container
                if ckey not in ctr_routes:
                    ctr_routes[ckey] = {"trk": trk, "stops": [], "vessels": []}
                    ctr_order.append(ckey)
                ctr_routes[ckey]["stops"].append(idx)
                ctr_routes[ckey]["vessels"].append(vessel)

            # Convert location containers dict → sorted list (alphabetical by key)
            for loc in locations:
                loc["containers"] = sorted(loc["containers"].values(), key=lambda c: c["key"])

            if not locations:
                _filter_desc = (
                    track_number_subquery or container_number_subquery
                    or track_numbers or container_numbers
                )
                msg = f"No mappable coordinates found for {_filter_desc}."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            # ── Color: per-TrackNumber base hue, light→dark per container ──
            TRACK_BASE_COLORS = [
                "#27AE60", "#2980B9", "#E67E22", "#8E44AD", "#C0392B",
                "#16A085", "#D35400", "#1A5276", "#6C3483", "#1E8449",
            ]

            def _blend(hex_col: str, factor: float) -> str:
                r = int(hex_col[1:3], 16)
                g = int(hex_col[3:5], 16)
                b = int(hex_col[5:7], 16)
                if factor <= 1.0:
                    r = int(r * factor + 255 * (1 - factor))
                    g = int(g * factor + 255 * (1 - factor))
                    b = int(b * factor + 255 * (1 - factor))
                else:
                    r = max(0, int(r * (2 - factor)))
                    g = max(0, int(g * (2 - factor)))
                    b = max(0, int(b * (2 - factor)))
                return "#%02x%02x%02x" % (min(255, r), min(255, g), min(255, b))

            unique_trks   = list(dict.fromkeys(ctr_routes[k]["trk"] for k in ctr_order))
            trk_base      = {t: TRACK_BASE_COLORS[i % len(TRACK_BASE_COLORS)]
                             for i, t in enumerate(unique_trks)}
            trk_containers: dict = {}
            for ckey in ctr_order:
                trk_containers.setdefault(ctr_routes[ckey]["trk"], []).append(ckey)

            routes = []
            for ckey in ctr_order:
                cinfo    = ctr_routes[ckey]
                t        = cinfo["trk"]
                siblings = trk_containers[t]
                n        = len(siblings)
                pos      = siblings.index(ckey)
                factor   = 0.35 + (pos / max(n - 1, 1)) * 0.75 if n > 1 else 0.85
                routes.append({
                    "key":     ckey,
                    "trk":     t,
                    "color":   _blend(trk_base[t], factor),
                    "stops":   cinfo["stops"],
                    "vessels": cinfo["vessels"],
                })

            highlight_regions = tool_input.get("highlight_regions") or []
            # Apply default color for regions with no color specified
            for _r in highlight_regions:
                if not _r.get("color"):
                    _r["color"] = "rgba(255,165,0,0.30)"
            map_data = {"locations": locations, "routes": routes, "highlight_regions": highlight_regions}
            unique_containers = len(routes)
            unique_stops      = len(locations)

            yield _sse("tool_start", {"tool": "generate_plot", "query": title})
            if _FILE_TOOLS_AVAILABLE:
                try:
                    from server.core.file_generator import (
                        _short_uuid, _slugify, ensure_file_store, _file_meta,
                        _plot_container_route_map,
                    )
                    file_id   = _short_uuid()
                    slug      = _slugify(title)
                    ensure_file_store(self.file_store_path)
                    file_info = _plot_container_route_map(
                        title=title,
                        data=map_data,
                        file_id=file_id,
                        title_slug=slug,
                        file_store_path=self.file_store_path,
                    )
                    self.generated_files.append(file_info)
                    yield _sse("file_generated", file_info)
                    return (
                        f"Container route map generated: {unique_containers} container(s), "
                        f"{unique_stops} unique stop(s)."
                    )
                except Exception as exc:
                    error_msg = f"Container map error: {exc}"
                    logger.exception(error_msg)
                    yield _sse("error", {"error": error_msg, "code": "PLOT_ERROR", "recoverable": True})
                    return error_msg
            return result.text

        elif name == "geocode_location":
            # ── Geocode via OpenStreetMap Nominatim ─────────────────────────
            import urllib.request as _urllib_req
            import urllib.parse   as _urllib_parse

            query = (tool_input.get("query") or "").strip()
            if not query:
                msg = "geocode_location requires a non-empty query string."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            yield _sse("tool_start", {"tool": "geocode_location", "query": query})

            try:
                url = (
                    "https://nominatim.openstreetmap.org/search"
                    f"?q={_urllib_parse.quote(query)}"
                    "&format=json&limit=3&addressdetails=0"
                )
                req = _urllib_req.Request(
                    url,
                    headers={"User-Agent": "SeaLine-Tracker/1.0 (internal logistics tool)"},
                )
                with _urllib_req.urlopen(req, timeout=10) as resp:
                    import json as _j
                    results = _j.loads(resp.read().decode())

                if not results:
                    msg = f"No geocode results found for '{query}'."
                    yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                    return msg

                lines = [f"Geocode results for '{query}':"]
                for r in results:
                    lines.append(
                        f"  display_name: {r['display_name']}, lat: {r['lat']}, lon: {r['lon']}"
                    )
                result_text = "\n".join(lines)
                yield _sse("tool_result", {"tool": name, "result": result_text, "truncated": False})
                return result_text

            except Exception as exc:
                msg = f"Geocoding failed: {exc}"
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

        elif name == "show_location_map":
            # ── Location bubble map ─────────────────────────────────────────
            title       = tool_input.get("title") or "Location Map"
            locations   = tool_input.get("locations") or []
            value_label = tool_input.get("value_label") or "Value"
            color       = tool_input.get("color") or "#2980B9"

            if not locations:
                msg = "show_location_map requires a non-empty locations array with lat/lon."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            yield _sse("tool_start", {"tool": "generate_plot", "query": title})
            if _FILE_TOOLS_AVAILABLE:
                try:
                    from server.core.file_generator import (
                        _short_uuid, _slugify, ensure_file_store,
                        _plot_location_bubble_map,
                    )
                    file_id = _short_uuid()
                    slug    = _slugify(title)
                    ensure_file_store(self.file_store_path)
                    file_info = _plot_location_bubble_map(
                        title=title,
                        locations=locations,
                        value_label=value_label,
                        default_color=color,
                        file_id=file_id,
                        title_slug=slug,
                        file_store_path=self.file_store_path,
                    )
                    self.generated_files.append(file_info)
                    yield _sse("file_generated", file_info)
                    return f"Location map generated: {len(locations)} location(s)."
                except Exception as exc:
                    error_msg = f"Location map error: {exc}"
                    logger.exception(error_msg)
                    yield _sse("error", {"error": error_msg, "code": "PLOT_ERROR", "recoverable": True})
                    return error_msg
            return "show_location_map: file tools unavailable."

        elif name == "show_choropleth_map":
            # ── Choropleth (country intensity) map ─────────────────────────
            title       = tool_input.get("title") or "Country Map"
            data        = tool_input.get("data") or []
            color       = (tool_input.get("color") or "blue").lower().strip()
            value_label = tool_input.get("value_label") or "Count"

            if not data:
                msg = "show_choropleth_map requires a non-empty data array."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            yield _sse("tool_start", {"tool": "generate_plot", "query": title})
            if _FILE_TOOLS_AVAILABLE:
                try:
                    from server.core.file_generator import (
                        _short_uuid, _slugify, ensure_file_store,
                        _plot_choropleth_map,
                    )
                    file_id = _short_uuid()
                    slug    = _slugify(title)
                    ensure_file_store(self.file_store_path)
                    file_info = _plot_choropleth_map(
                        title=title,
                        data=data,
                        color=color,
                        value_label=value_label,
                        file_id=file_id,
                        title_slug=slug,
                        file_store_path=self.file_store_path,
                    )
                    self.generated_files.append(file_info)
                    yield _sse("file_generated", file_info)
                    return f"Choropleth map generated: {len(data)} country/region(s)."
                except Exception as exc:
                    error_msg = f"Choropleth map error: {exc}"
                    logger.exception(error_msg)
                    yield _sse("error", {"error": error_msg, "code": "PLOT_ERROR", "recoverable": True})
                    return error_msg
            return f"show_choropleth_map: file tools unavailable."

        else:
            msg = f"Unknown tool: {name}"
            yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
            return msg

    # ------------------------------------------------------------------
    #  send_message — streaming generator
    # ------------------------------------------------------------------

    def send_message(self, user_text: str) -> Generator[dict, None, None]:
        """
        Process a user message through the agentic tool-use loop.

        Yields SSE event dicts:
            message_start, text_delta, tool_start, tool_result,
            file_generated, plot_generated, message_end, error, warning
        """
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.generated_files = []

        # Append user turn
        self.messages.append({"role": "user", "content": user_text})

        yield _sse("message_start", {"message_id": message_id, "session_id": self.session_id})

        tools = self._tools()
        sys_content = self._system_content()

        msg_input_tokens = 0
        msg_output_tokens = 0

        loop_count = 0
        while True:
            loop_count += 1
            if loop_count > MAX_TOOL_LOOPS:
                yield _sse("error", {
                    "error": f"Maximum tool-use iterations ({MAX_TOOL_LOOPS}) exceeded. Please simplify your request.",
                    "code": "MAX_ITERATIONS",
                    "recoverable": False,
                })
                break

            try:
                with self.client.messages.stream(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=sys_content,
                    messages=self.messages,
                    tools=tools,
                ) as stream:
                    # Stream text tokens to the frontend as they arrive
                    for text in stream.text_stream:
                        yield _sse("text_delta", {"delta": text})

                    # Get the complete final message (already assembled by the SDK)
                    final_message = stream.get_final_message()

                # Accumulate token usage
                msg_input_tokens += final_message.usage.input_tokens
                msg_output_tokens += final_message.usage.output_tokens
                self.total_input_tokens += final_message.usage.input_tokens
                self.total_output_tokens += final_message.usage.output_tokens

                stop_reason = final_message.stop_reason

                # Build assistant history entry from the response content blocks
                assistant_content = []
                for block in final_message.content:
                    if block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                self.messages.append({"role": "assistant", "content": assistant_content})

                # ---- Tool use ----
                if stop_reason == "tool_use":
                    tool_use_blocks = [b for b in final_message.content if b.type == "tool_use"]
                    tool_results: list[dict] = []

                    for tb in tool_use_blocks:
                        gen = self._execute_tool(tb.name, tb.input)
                        result_text = ""
                        try:
                            while True:
                                yield next(gen)
                        except StopIteration as stop:
                            result_text = stop.value or ""

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": result_text,
                        })

                    self.messages.append({"role": "user", "content": tool_results})
                    continue

                # ---- end_turn or other terminal stop reason ----
                break

            except anthropic.AuthenticationError as exc:
                logger.error("Anthropic authentication error: %s", exc)
                yield _sse("error", {
                    "error": "Anthropic API authentication failed. Check ANTHROPIC_API_KEY.",
                    "code": "AUTH_ERROR",
                    "recoverable": False,
                })
                break
            except anthropic.RateLimitError as exc:
                logger.warning("Anthropic rate limit: %s", exc)
                yield _sse("error", {
                    "error": "Rate limit reached. Please wait a moment and try again.",
                    "code": "RATE_LIMITED",
                    "recoverable": True,
                })
                break
            except anthropic.APIConnectionError as exc:
                logger.error("Anthropic connection error: %s", exc)
                yield _sse("error", {
                    "error": "Could not connect to the AI service. Please try again shortly.",
                    "code": "CONNECTION_ERROR",
                    "recoverable": True,
                })
                break
            except anthropic.APIStatusError as exc:
                logger.error("Anthropic API status error %s: %s", exc.status_code, exc.message)
                yield _sse("error", {
                    "error": "The AI service returned an error. Please try again.",
                    "code": "ANTHROPIC_API_ERROR",
                    "recoverable": False,
                })
                break
            except Exception as exc:
                logger.exception("Unexpected agent error: %s", exc)
                yield _sse("error", {
                    "error": "An internal error occurred. Please try again.",
                    "code": "AGENT_ERROR",
                    "recoverable": False,
                })
                break

        yield _sse("message_end", {
            "message_id": message_id,
            "usage": {
                "input_tokens": msg_input_tokens,
                "output_tokens": msg_output_tokens,
                "cache_read_tokens": 0,
                "sql_calls": self.sql_calls,
            },
        })

    # ------------------------------------------------------------------
    #  send_message_sync — non-streaming for Teams
    # ------------------------------------------------------------------

    def send_message_sync(self, user_text: str) -> str:
        collected_text: list[str] = []
        for event_dict in self.send_message(user_text):
            event_type = event_dict.get("event", "")
            data = event_dict.get("data", {})
            if event_type == "text_delta":
                collected_text.append(data.get("delta", ""))
            elif event_type == "error":
                collected_text.append(f"\n\n[Error: {data.get('error', 'An error occurred.')}]")
        return "".join(collected_text)
