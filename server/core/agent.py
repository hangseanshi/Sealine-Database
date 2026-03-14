"""
Agent Core — Azure OpenAI-powered agentic loop for Sealine Data Chat.

Replaces the Anthropic Claude agent with Azure OpenAI (GPT-4o).
The SSE event interface is identical — the frontend is unchanged.

Key differences from the Anthropic version:
  - Client: AzureOpenAI instead of anthropic.Anthropic
  - Tool definitions wrapped in {"type": "function", "function": {...}}
  - Tool results sent as role:"tool" messages, not role:"user" content blocks
  - Streaming uses chunk.choices[0].delta; tool call args accumulated across chunks
  - No prompt caching (cache_control removed)
  - No thinking blocks
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Generator

import httpx
from openai import AzureOpenAI, AuthenticationError, RateLimitError, APIConnectionError, APIStatusError

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


CONTAINER_ROUTES_TOOL = {
    "name": "show_container_routes",
    "description": (
        "Generate an interactive map showing the route of every container under one or more "
        "tracking numbers, or for specific container numbers. "
        "Call this tool WHENEVER the user asks about container routes, container movements, "
        "or wants to see containers on a map. "
        "Each container gets its own coloured route with arrow lines between stops."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "track_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more tracking numbers (e.g. DALA71196300). Use when user provides a tracking number.",
            },
            "container_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more container numbers (e.g. GAOU6335790). Use when user provides specific container numbers.",
            },
            "title": {
                "type": "string",
                "description": "Map title shown at the top.",
            },
        },
    },
}


def _get_plot_tool_def() -> dict:
    return GENERATE_PLOT_TOOL if _FILE_TOOLS_AVAILABLE else _FALLBACK_PLOT_TOOL


def _get_pdf_tool_def() -> dict:
    return GENERATE_PDF_TOOL if _FILE_TOOLS_AVAILABLE else _FALLBACK_PDF_TOOL


def _get_excel_tool_def() -> dict:
    return GENERATE_EXCEL_TOOL if _FILE_TOOLS_AVAILABLE else _FALLBACK_EXCEL_TOOL


def _to_openai_tool(tool_def: dict) -> dict:
    """Convert Anthropic-style tool definition to OpenAI function format."""
    return {
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def.get("description", ""),
            "parameters": tool_def.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


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
    Azure OpenAI-powered agent that yields SSE event dicts.

    Constructor args match the Anthropic version exactly so messages.py
    requires no changes.
    """

    def __init__(
        self,
        model: str = "gpt-4o-03252025",
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
        self.client = AzureOpenAI(
            azure_endpoint=cfg.AZURE_OPENAI_ENDPOINT,
            api_version=cfg.AZURE_OPENAI_API_VERSION,
            api_key=cfg.AZURE_OPENAI_API_KEY,
            http_client=httpx.Client(verify=False),
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
            tools.append(_to_openai_tool(SQL_TOOL))
        tools.append(_to_openai_tool(_get_plot_tool_def()))
        tools.append(_to_openai_tool(_get_pdf_tool_def()))
        tools.append(_to_openai_tool(_get_excel_tool_def()))
        tools.append(_to_openai_tool(CONTAINER_ROUTES_TOOL))
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

                    # Guard: for map type, refuse to render if no coordinate data supplied
                    if tool_input.get("plot_type") == "map":
                        _has_coords = bool(data.get("lat") or data.get("lon") or data.get("routes"))
                        if not _has_coords:
                            err = (
                                "Map generation skipped: no coordinate data was provided. "
                                "Please include lat/lon arrays or a routes array with lat/lon in the data parameter."
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

        elif name == "show_container_routes":
            # ── Dedicated container-route map tool ─────────────────────────
            # Runs its own SQL against Sealine_Container_Event so the AI never
            # needs to choose between Sealine_Locations and Container_Event.
            track_numbers = tool_input.get("track_numbers") or []
            container_numbers = tool_input.get("container_numbers") or []
            title = tool_input.get("title") or "Container Routes"

            if not track_numbers and not container_numbers:
                msg = "show_container_routes requires track_numbers or container_numbers."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            if track_numbers:
                vals = ", ".join(f"'{v}'" for v in track_numbers)
                where = f"e.TrackNumber IN ({vals})"
            else:
                vals = ", ".join(f"'{v}'" for v in container_numbers)
                where = f"e.Container_NUMBER IN ({vals})"

            sql = (
                "SELECT e.Container_NUMBER, "
                "TRY_CAST(COALESCE(f.Lat, l.Lat) AS FLOAT) AS Lat, "
                "TRY_CAST(COALESCE(f.Lng, l.Lng) AS FLOAT) AS Lng, "
                "COALESCE(f.Name, l.Name) AS LocationName, "
                "MIN(CONVERT(VARCHAR(10), e.Date, 120)) AS FirstDate, "
                "MAX(CAST(e.Actual AS INT)) AS IsActual "
                "FROM Sealine_Container_Event e "
                "LEFT JOIN Sealine_Facilities f "
                "  ON e.TrackNumber = f.TrackNumber AND e.Facility = f.Id "
                "LEFT JOIN Sealine_Locations l "
                "  ON e.TrackNumber = l.TrackNumber AND e.Location = l.Id "
                f"WHERE {where} AND e.DeletedDt IS NULL "
                "AND COALESCE(f.Lat, l.Lat) IS NOT NULL "
                "AND COALESCE(f.Lng, l.Lng) IS NOT NULL "
                "GROUP BY e.Container_NUMBER, "
                "  COALESCE(f.Lat, l.Lat), COALESCE(f.Lng, l.Lng), "
                "  COALESCE(f.Name, l.Name) "
                "ORDER BY e.Container_NUMBER, MIN(TRY_CAST(e.Order_id AS INT)) ASC"
            )

            yield _sse("tool_start", {"tool": "execute_sql", "query": sql})
            result = execute_sql(sql)
            self.sql_calls += 1
            yield _sse("tool_result", {"tool": "execute_sql", "result": result.text, "truncated": result.truncated})

            # Parse result rows into map data
            lats, lons, labels, groups = [], [], [], []
            for line in result.text.splitlines():
                line = line.strip()
                if not line or line.startswith("-") or line.startswith("Container"):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                try:
                    cnum = parts[0]
                    lat = float(parts[1])
                    lon = float(parts[2])
                    loc = parts[3]
                    date_str = parts[4] if len(parts) > 4 else ""
                    is_actual = parts[5] if len(parts) > 5 else "0"
                    status = "Actual" if str(is_actual).strip() == "1" else "Estimated"
                    label = f"{cnum}<br>{loc}<br>{date_str} ({status})" if date_str else f"{cnum}<br>{loc}"
                    lats.append(lat); lons.append(lon)
                    labels.append(label); groups.append(cnum)
                except (ValueError, IndexError):
                    continue

            if not lats:
                msg = f"No container route data found for {track_numbers or container_numbers}."
                yield _sse("tool_result", {"tool": name, "result": msg, "truncated": False})
                return msg

            yield _sse("tool_start", {"tool": "generate_plot", "query": title})
            if _FILE_TOOLS_AVAILABLE:
                try:
                    file_info = _generate_plot(
                        plot_type="map",
                        title=title,
                        data={"lat": lats, "lon": lons, "labels": labels, "groups": groups, "arrows": True},
                        interactive=True,
                        file_store_path=self.file_store_path,
                    )
                    self.generated_files.append(file_info)
                    yield _sse("file_generated", file_info)
                    return f"Container route map generated with {len(set(groups))} containers. Raw data:\n{result.text}"
                except Exception as exc:
                    error_msg = f"Container map error: {exc}"
                    logger.exception(error_msg)
                    yield _sse("error", {"error": error_msg, "code": "PLOT_ERROR", "recoverable": True})
                    return error_msg
            return result.text

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

        # Append user turn (OpenAI format)
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
                # Accumulate streaming response
                text_chunks: list[str] = []
                tool_calls_acc: dict[int, dict] = {}  # index -> {id, name, arguments}
                finish_reason: str | None = None

                stream = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "system", "content": sys_content}] + self.messages,
                    tools=tools,
                    stream=True,
                    stream_options={"include_usage": True},
                )

                for chunk in stream:
                    # Usage chunk (final chunk with no choices)
                    if not chunk.choices:
                        if hasattr(chunk, "usage") and chunk.usage:
                            msg_input_tokens += chunk.usage.prompt_tokens or 0
                            msg_output_tokens += chunk.usage.completion_tokens or 0
                            self.total_input_tokens += chunk.usage.prompt_tokens or 0
                            self.total_output_tokens += chunk.usage.completion_tokens or 0
                        continue

                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    delta = choice.delta

                    # Text content
                    if delta.content:
                        text_chunks.append(delta.content)
                        yield _sse("text_delta", {"delta": delta.content})

                    # Tool call accumulation
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc.id:
                                tool_calls_acc[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls_acc[idx]["name"] += tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc.function.arguments

                # Build assistant message for history
                assistant_content = "".join(text_chunks) or None
                assistant_msg: dict = {"role": "assistant", "content": assistant_content}
                if tool_calls_acc:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments"]},
                        }
                        for tc in sorted(tool_calls_acc.values(), key=lambda x: list(tool_calls_acc.keys()).index(
                            next(k for k, v in tool_calls_acc.items() if v is x)
                        ))
                    ]
                self.messages.append(assistant_msg)

                # ---- Tool use ----
                if finish_reason == "tool_calls" and tool_calls_acc:
                    tool_result_msgs: list[dict] = []

                    for tc in tool_calls_acc.values():
                        try:
                            tool_input = json.loads(tc["arguments"])
                        except json.JSONDecodeError:
                            tool_input = {}

                        gen = self._execute_tool(tc["name"], tool_input)
                        result_text = ""
                        try:
                            while True:
                                yield next(gen)
                        except StopIteration as stop:
                            result_text = stop.value or ""

                        tool_result_msgs.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_text,
                        })

                    self.messages.extend(tool_result_msgs)
                    continue

                # ---- end_turn ----
                break

            except AuthenticationError as exc:
                logger.error("Azure OpenAI authentication error: %s", exc)
                yield _sse("error", {
                    "error": "Azure OpenAI authentication failed. Check server configuration.",
                    "code": "AUTH_ERROR",
                    "recoverable": False,
                })
                break
            except RateLimitError as exc:
                logger.warning("Azure OpenAI rate limit: %s", exc)
                yield _sse("error", {
                    "error": "Rate limit reached. Please wait a moment and try again.",
                    "code": "RATE_LIMITED",
                    "recoverable": True,
                })
                break
            except APIConnectionError as exc:
                logger.error("Azure OpenAI connection error: %s", exc)
                yield _sse("error", {
                    "error": "Could not connect to the AI service. Please try again shortly.",
                    "code": "CONNECTION_ERROR",
                    "recoverable": True,
                })
                break
            except APIStatusError as exc:
                logger.error("Azure OpenAI status error %s: %s", exc.status_code, exc.message)
                yield _sse("error", {
                    "error": "The AI service returned an error. Please try again.",
                    "code": "OPENAI_API_ERROR",
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
