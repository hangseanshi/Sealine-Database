"""
Agent Core — Refactored ClaudeChat class for API usage.

This is the heart of the Sealine Data Chat backend.  It preserves the exact
agentic tool-use loop from claude_desktop.py (stream -> detect tool_use ->
execute -> loop back) but replaces all terminal I/O with a generator that
yields SSE event dicts.

Key differences from the terminal version:
  - print() calls   -> yield SSE event dicts
  - input() calls   -> method parameter (user_text)
  - ANSI formatting  -> removed (frontend handles presentation)
  - File output      -> delegates to file_generator, yields file/plot events
  - Synchronous path -> send_message_sync() for Teams (non-streaming)
"""

from __future__ import annotations

import logging
import uuid
from typing import Generator

import anthropic
import httpx

from server.core.sql_executor import execute_sql, MAX_ROWS

# Graceful import of file_generator — another agent builds this module.
# If it is not available yet, the file-generation tools are simply omitted.
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

# Maximum number of tool-use loop iterations to prevent infinite loops
# and unbounded API costs.
MAX_TOOL_LOOPS = 15


# ---------------------------------------------------------------------------
#  Tool definitions
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

# Inline tool definitions for when the file_generator module is not yet built.
# These match the PRD Section 10.1 exactly.
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
                "description": (
                    "Chart data as JSON.  Structure depends on plot_type. "
                    'Example for bar: {"labels": [...], "values": [...]}.'
                ),
            },
            "interactive": {
                "type": "boolean",
                "description": (
                    "If true, generate interactive Plotly HTML. "
                    "If false, generate static matplotlib PNG."
                ),
                "default": False,
            },
        },
        "required": ["plot_type", "title", "data"],
    },
}

_FALLBACK_PDF_TOOL = {
    "name": "generate_pdf",
    "description": (
        "Generate a PDF report. Content is rendered from a simple template "
        "with a title, optional summary text, and a data table."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Report title."},
            "summary": {
                "type": "string",
                "description": "Optional summary paragraph shown above the data table.",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column headers for the data table.",
            },
            "rows": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
                "description": "Table rows. Each row is an array of string values.",
            },
            "filename": {
                "type": "string",
                "description": "Output filename (without extension).",
            },
        },
        "required": ["title", "columns", "rows"],
    },
}

_FALLBACK_EXCEL_TOOL = {
    "name": "generate_excel",
    "description": (
        "Generate a formatted Excel (.xlsx) report with blue header row, "
        "frozen top row, and auto-sized columns."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Sheet name / report title."},
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column headers.",
            },
            "rows": {
                "type": "array",
                "items": {"type": "array"},
                "description": "Data rows.",
            },
            "filename": {
                "type": "string",
                "description": "Output filename (without extension).",
            },
        },
        "required": ["title", "columns", "rows"],
    },
}


def _get_plot_tool_def() -> dict:
    if _FILE_TOOLS_AVAILABLE:
        return GENERATE_PLOT_TOOL
    return _FALLBACK_PLOT_TOOL


def _get_pdf_tool_def() -> dict:
    if _FILE_TOOLS_AVAILABLE:
        return GENERATE_PDF_TOOL
    return _FALLBACK_PDF_TOOL


def _get_excel_tool_def() -> dict:
    if _FILE_TOOLS_AVAILABLE:
        return GENERATE_EXCEL_TOOL
    return _FALLBACK_EXCEL_TOOL


# ---------------------------------------------------------------------------
#  SSE Event dict helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> dict:
    """Build an SSE event dict."""
    return {"event": event, "data": data}


# ---------------------------------------------------------------------------
#  Agent class
# ---------------------------------------------------------------------------

class SealineAgent:
    """
    API-compatible Claude agent that yields SSE event dicts.

    Preserves the full agentic tool-use loop from claude_desktop.py:
      stream -> detect tool_use -> execute tool -> feed result back -> loop

    Constructor args:
        model:           Claude model ID (e.g. "claude-haiku-4-5")
        system_prompt:   Base system prompt text
        max_tokens:      Max output tokens per API call
        docs_text:       Concatenated markdown context text
        docs_files:      List of loaded markdown filenames
        db_enabled:      Whether the execute_sql tool is available
        session_id:      UUID of the owning session (included in events)
        file_store_path: Filesystem directory where generated files are stored
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        system_prompt: str = (
            "You are Claude, a helpful AI assistant and data analyst "
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
        # Anthropic client — keep httpx.Client(verify=False) for SSL bypass
        self.client = anthropic.Anthropic(
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

        # Conversation history (Anthropic messages format)
        self.messages: list[dict] = messages if messages is not None else []

        # Usage tracking
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.cache_hits: int = 0
        self.sql_calls: int = 0

        # Files generated during this message exchange
        self.generated_files: list[dict] = []

    # ------------------------------------------------------------------
    #  System prompt blocks (with ephemeral prompt caching)
    # ------------------------------------------------------------------

    def _system_blocks(self) -> list[dict] | str:
        """
        Build the system blocks array for the Anthropic API.

        Uses ephemeral cache_control on the docs block so that subsequent
        turns in the same session benefit from prompt caching.
        """
        tool_instructions: list[str] = []
        if self.db_enabled:
            tool_instructions.append(
                "You have access to the `execute_sql` tool which runs live queries "
                "against the Sealine searates SQL Server database. Use it whenever the "
                "user asks for data, counts, reports, or anything requiring live results."
            )
        if True:  # File tools are always defined (fallbacks exist)
            tool_instructions.append(
                "You can generate charts with `generate_plot`, PDF reports with "
                "`generate_pdf`, and Excel spreadsheets with `generate_excel`. "
                "Use these tools when the user asks for visualizations or downloadable files."
            )

        db_note = "\n\n" + "\n".join(tool_instructions) if tool_instructions else ""
        base = self.system_prompt + db_note

        if not self.docs_text:
            return base

        return [
            {"type": "text", "text": base},
            {
                "type": "text",
                "text": (
                    "# Sealine-Database Reference Documents\n\n"
                    "The following Markdown files have been loaded from the repository. "
                    "Use them as your primary reference for schema, relationships, "
                    "connection details, and saved reports.\n\n"
                    + self.docs_text
                ),
                "cache_control": {"type": "ephemeral"},
            },
        ]

    # ------------------------------------------------------------------
    #  Tool list
    # ------------------------------------------------------------------

    def _tools(self) -> list[dict]:
        """Return the list of tool definitions to pass to the API."""
        tools: list[dict] = []
        if self.db_enabled:
            tools.append(SQL_TOOL)
        tools.append(_get_plot_tool_def())
        tools.append(_get_pdf_tool_def())
        tools.append(_get_excel_tool_def())
        return tools

    # ------------------------------------------------------------------
    #  Tool executor
    # ------------------------------------------------------------------

    def _execute_tool(
        self, name: str, tool_input: dict
    ) -> Generator[dict, None, str]:
        """
        Execute a tool call and yield SSE events during execution.

        Returns the text result to feed back to Claude as a tool_result.
        Yields tool_start, tool_result, file_generated, or plot_generated events.
        """
        if name == "execute_sql":
            query = tool_input.get("query", "")
            yield _sse("tool_start", {"tool": "execute_sql", "query": query})

            result = execute_sql(query)
            self.sql_calls += 1

            yield _sse(
                "tool_result",
                {
                    "tool": "execute_sql",
                    "result": result.text,
                    "truncated": result.truncated,
                },
            )
            return result.text

        elif name == "generate_plot":
            yield _sse(
                "tool_start",
                {
                    "tool": "generate_plot",
                    "query": tool_input.get("title", ""),
                },
            )
            if _FILE_TOOLS_AVAILABLE:
                try:
                    file_info = _generate_plot(
                        plot_type=tool_input["plot_type"],
                        title=tool_input["title"],
                        data=tool_input["data"],
                        interactive=tool_input.get("interactive", False),
                        x_label=tool_input.get("x_label"),
                        y_label=tool_input.get("y_label"),
                        file_store_path=self.file_store_path,
                    )
                    self.generated_files.append(file_info)
                    event_type = (
                        "plot_generated"
                        if file_info.get("file_type", "").startswith("image/")
                        else "file_generated"
                    )
                    yield _sse(event_type, file_info)
                    return f"Plot generated: {file_info.get('filename', 'chart')}"
                except Exception as exc:
                    error_msg = f"Plot generation error: {exc}"
                    logger.exception(error_msg)
                    yield _sse(
                        "error",
                        {"error": error_msg, "code": "PLOT_ERROR", "recoverable": True},
                    )
                    return error_msg
            else:
                msg = (
                    "Plot generation is not available yet "
                    "(file_generator module not installed)."
                )
                yield _sse(
                    "tool_result",
                    {"tool": "generate_plot", "result": msg, "truncated": False},
                )
                return msg

        elif name == "generate_pdf":
            yield _sse(
                "tool_start",
                {
                    "tool": "generate_pdf",
                    "query": tool_input.get("title", ""),
                },
            )
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
                    yield _sse(
                        "error",
                        {"error": error_msg, "code": "PDF_ERROR", "recoverable": True},
                    )
                    return error_msg
            else:
                msg = (
                    "PDF generation is not available yet "
                    "(file_generator module not installed)."
                )
                yield _sse(
                    "tool_result",
                    {"tool": "generate_pdf", "result": msg, "truncated": False},
                )
                return msg

        elif name == "generate_excel":
            yield _sse(
                "tool_start",
                {
                    "tool": "generate_excel",
                    "query": tool_input.get("title", ""),
                },
            )
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
                    return (
                        f"Excel generated: {file_info.get('filename', 'report.xlsx')}"
                    )
                except Exception as exc:
                    error_msg = f"Excel generation error: {exc}"
                    logger.exception(error_msg)
                    yield _sse(
                        "error",
                        {
                            "error": error_msg,
                            "code": "EXCEL_ERROR",
                            "recoverable": True,
                        },
                    )
                    return error_msg
            else:
                msg = (
                    "Excel generation is not available yet "
                    "(file_generator module not installed)."
                )
                yield _sse(
                    "tool_result",
                    {"tool": "generate_excel", "result": msg, "truncated": False},
                )
                return msg

        else:
            msg = f"Unknown tool: {name}"
            yield _sse(
                "tool_result",
                {"tool": name, "result": msg, "truncated": False},
            )
            return msg

    # ------------------------------------------------------------------
    #  send_message — streaming generator (main API path)
    # ------------------------------------------------------------------

    def send_message(self, user_text: str) -> Generator[dict, None, None]:
        """
        Process a user message through the agentic tool-use loop.

        Yields SSE event dicts:
            message_start, text_delta, tool_start, tool_result,
            file_generated, plot_generated, message_end, error
        """
        message_id = f"msg_{uuid.uuid4().hex[:12]}"

        # Reset generated_files for this message (avoid accumulation across
        # multiple send_message calls on the same agent instance).
        self.generated_files = []

        # Append user turn
        self.messages.append({"role": "user", "content": user_text})

        yield _sse(
            "message_start",
            {"message_id": message_id, "session_id": self.session_id},
        )

        tools = self._tools()

        # Track usage for this message
        msg_input_tokens = 0
        msg_output_tokens = 0
        msg_cache_read = 0

        # ---- Agentic loop (identical structure to original) ----
        loop_count = 0
        while True:
            loop_count += 1
            if loop_count > MAX_TOOL_LOOPS:
                yield _sse("error", {
                    "error": f"Maximum tool-use iterations ({MAX_TOOL_LOOPS}) exceeded. "
                             "Please simplify your request.",
                    "code": "MAX_ITERATIONS",
                    "recoverable": False,
                })
                break
            try:
                final = None

                try:
                    # Primary streaming call
                    with self.client.messages.stream(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        system=self._system_blocks(),
                        tools=tools,
                        messages=self.messages,
                    ) as stream:
                        for event in stream:
                            if event.type == "content_block_start":
                                if event.content_block.type == "thinking":
                                    yield _sse(
                                        "thinking",
                                        {"content": ""},
                                    )
                            elif event.type == "content_block_delta":
                                if event.delta.type == "text_delta":
                                    yield _sse(
                                        "text_delta",
                                        {"delta": event.delta.text},
                                    )
                                elif event.delta.type == "thinking_delta":
                                    yield _sse(
                                        "thinking",
                                        {"content": event.delta.thinking},
                                    )
                        final = stream.get_final_message()

                except anthropic.BadRequestError as bad_exc:
                    # Haiku / older model — may not support thinking.
                    # Only retry if the error is about thinking; otherwise raise.
                    if "thinking" not in str(bad_exc).lower():
                        raise
                    logger.info("BadRequestError (thinking?), retrying without thinking")
                    with self.client.messages.stream(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        system=self._system_blocks(),
                        tools=tools,
                        messages=self.messages,
                    ) as stream:
                        for text in stream.text_stream:
                            yield _sse("text_delta", {"delta": text})
                        final = stream.get_final_message()

                # ---- Update usage counters ----
                msg_input_tokens += final.usage.input_tokens
                msg_output_tokens += final.usage.output_tokens
                cache_read = (
                    getattr(final.usage, "cache_read_input_tokens", 0) or 0
                )
                if cache_read:
                    msg_cache_read += cache_read
                    self.cache_hits += 1

                self.total_input_tokens += final.usage.input_tokens
                self.total_output_tokens += final.usage.output_tokens

                # Append assistant turn (full content list with tool_use blocks)
                self.messages.append(
                    {"role": "assistant", "content": final.content}
                )

                # ---- Check if Claude wants to use a tool ----
                if final.stop_reason == "tool_use":
                    tool_results: list[dict] = []
                    for block in final.content:
                        if block.type == "tool_use":
                            # _execute_tool is itself a generator that yields
                            # SSE events and returns the result text.
                            gen = self._execute_tool(block.name, block.input)
                            result_text = ""
                            try:
                                while True:
                                    event_dict = next(gen)
                                    yield event_dict
                            except StopIteration as stop:
                                result_text = stop.value or ""

                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_text,
                                }
                            )

                    # Feed results back and loop
                    self.messages.append(
                        {"role": "user", "content": tool_results}
                    )
                    continue

                # ---- end_turn: done ----
                break

            except anthropic.AuthenticationError as exc:
                logger.error("Anthropic authentication error: %s", exc)
                yield _sse(
                    "error",
                    {
                        "error": "Anthropic API authentication failed. Check server configuration.",
                        "code": "AUTH_ERROR",
                        "recoverable": False,
                    },
                )
                break
            except anthropic.RateLimitError as exc:
                logger.warning("Anthropic rate limit: %s", exc)
                yield _sse(
                    "error",
                    {
                        "error": "Rate limit reached. Please wait a moment and try again.",
                        "code": "RATE_LIMITED",
                        "recoverable": True,
                    },
                )
                break
            except anthropic.APIConnectionError as exc:
                logger.error("Anthropic connection error: %s", exc)
                yield _sse(
                    "error",
                    {
                        "error": "Could not connect to the AI service. Please try again shortly.",
                        "code": "CONNECTION_ERROR",
                        "recoverable": True,
                    },
                )
                break
            except anthropic.APIStatusError as exc:
                logger.error("Anthropic API status error %s: %s", exc.status_code, exc.message)
                yield _sse(
                    "error",
                    {
                        "error": "The AI service returned an error. Please try again.",
                        "code": "CLAUDE_API_ERROR",
                        "recoverable": False,
                    },
                )
                break
            except Exception as exc:
                logger.exception("Unexpected agent error")
                yield _sse(
                    "error",
                    {
                        "error": "An internal error occurred. Please try again.",
                        "code": "AGENT_ERROR",
                        "recoverable": False,
                    },
                )
                break

        # ---- Emit message_end ----
        yield _sse(
            "message_end",
            {
                "message_id": message_id,
                "usage": {
                    "input_tokens": msg_input_tokens,
                    "output_tokens": msg_output_tokens,
                    "cache_read_tokens": msg_cache_read,
                    "sql_calls": self.sql_calls,
                },
            },
        )

    # ------------------------------------------------------------------
    #  send_message_sync — non-streaming for Teams
    # ------------------------------------------------------------------

    def send_message_sync(self, user_text: str) -> str:
        """
        Non-streaming message processing for Teams integration.

        Runs the full agentic loop but collects text deltas into a single
        string instead of streaming them.  Returns the final assembled
        text response.
        """
        collected_text: list[str] = []

        for event_dict in self.send_message(user_text):
            event_type = event_dict.get("event", "")
            data = event_dict.get("data", {})

            if event_type == "text_delta":
                collected_text.append(data.get("delta", ""))
            elif event_type == "error":
                error_msg = data.get("error", "An error occurred.")
                collected_text.append(f"\n\n[Error: {error_msg}]")

        return "".join(collected_text)
