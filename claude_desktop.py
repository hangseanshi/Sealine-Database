#!/usr/bin/env python3
"""
claude_desktop.py — A terminal chat interface simulating Claude for Desktop.
Supports multi-turn conversation history, streaming responses, auto-loads
all Markdown (.md) files as cached context, and can execute live SQL queries
against the Sealine database (Option B).

Usage:
    python claude_desktop.py
    python claude_desktop.py --model claude-sonnet-4-6
    python claude_desktop.py --no-db       # disable live SQL tool
    python claude_desktop.py --no-docs     # skip MD loading

Requirements:
    pip install anthropic httpx pyodbc python-dotenv
    Set ANTHROPIC_API_KEY environment variable or create .env file.
"""

import os
import sys
import glob
import argparse
import textwrap
import httpx
import anthropic
from dotenv import load_dotenv

# Load environment variables from .env in the same directory as this script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_SCRIPT_DIR, ".env"))

try:
    import pyodbc
    PYODBC_AVAILABLE = True
except ImportError:
    PYODBC_AVAILABLE = False

# ── ANSI colour helpers ────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
MAGENTA = "\033[35m"
BLUE    = "\033[34m"

def supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

def c(text: str, *codes: str) -> str:
    if not supports_color():
        return text
    return "".join(codes) + text + RESET


# ── DB connection ──────────────────────────────────────────────────────────────
DB_CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=ushou102-exap1;"
    "DATABASE=searates;"
    "UID=sean;"
    "PWD=4peiling;"
)
MAX_ROWS = 500  # cap rows returned to Claude to avoid token explosion


def run_sql(query: str) -> str:
    """Execute a SQL query and return results as formatted text."""
    q = query.strip()
    # Safety: only allow read operations
    first_word = q.split()[0].upper() if q.split() else ""
    if first_word not in ("SELECT", "WITH", "EXEC", "EXECUTE"):
        return "ERROR: Only SELECT / WITH / EXEC queries are permitted."
    try:
        conn = pyodbc.connect(DB_CONN_STR, timeout=30)
        cursor = conn.cursor()
        cursor.execute(q)

        if cursor.description is None:
            conn.close()
            return "Query executed successfully (no rows returned)."

        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        conn.close()

        if not rows:
            return f"Columns: {', '.join(cols)}\n(0 rows)"

        # Build a simple pipe-delimited table
        col_widths = [len(col) for col in cols]
        str_rows = []
        for row in rows:
            str_row = [str(v) if v is not None else "NULL" for v in row]
            for i, val in enumerate(str_row):
                col_widths[i] = max(col_widths[i], min(len(val), 50))
            str_rows.append(str_row)

        def fmt_row(values):
            return "  ".join(v[:50].ljust(col_widths[i]) for i, v in enumerate(values))

        header = fmt_row(cols)
        separator = "  ".join("-" * w for w in col_widths)
        lines = [header, separator] + [fmt_row(r) for r in str_rows]
        if truncated:
            lines.append(f"\n(Showing first {MAX_ROWS} rows — results truncated)")
        else:
            lines.append(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")

        return "\n".join(lines)

    except Exception as e:
        return f"SQL ERROR: {e}"


# ── Tool definition ────────────────────────────────────────────────────────────
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
                "description": "The SQL query to execute (SELECT or WITH only)."
            }
        },
        "required": ["query"]
    }
}


# ── Markdown loader ────────────────────────────────────────────────────────────
def load_md_files(search_root: str) -> tuple[str, list[str]]:
    pattern = os.path.join(search_root, "**", "*.md")
    paths = sorted(glob.glob(pattern, recursive=True))
    if not paths:
        return "", []
    sections, loaded = [], []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
            rel = os.path.relpath(path, search_root)
            sections.append(f"## File: {rel}\n\n{content}")
            loaded.append(rel)
        except OSError:
            pass
    return "\n\n---\n\n".join(sections), loaded


# ── Banner ─────────────────────────────────────────────────────────────────────
def make_banner() -> str:
    return f"""
  {c('╔══════════════════════════════════════════╗', CYAN, BOLD)}
  {c('║', CYAN, BOLD)}  {c('Claude for Desktop  (terminal edition)', BOLD)}  {c('║', CYAN, BOLD)}
  {c('╚══════════════════════════════════════════╝', CYAN, BOLD)}

  {c('Commands:', DIM)}
  {c('  /clear', YELLOW)}   — clear conversation history
  {c('  /history', YELLOW)} — show message count & token usage
  {c('  /docs', YELLOW)}    — list loaded Markdown files
  {c('  /system', YELLOW)}  — view/set the system prompt
  {c('  /quit', YELLOW)}    — exit  (or Ctrl-C / Ctrl-D)
  {c('  /help', YELLOW)}    — show this help

  Press {c('Enter', BOLD)} twice on an empty line to submit multi-line input.
"""


# ── Conversation manager ───────────────────────────────────────────────────────
class ClaudeChat:
    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        base_system: str = "You are Claude, a helpful AI assistant made by Anthropic.",
        max_tokens: int = 8192,
        docs_text: str = "",
        docs_files: list[str] | None = None,
        db_enabled: bool = True,
    ):
        self.client = anthropic.Anthropic(
            http_client=httpx.Client(verify=False)
        )
        self.model = model
        self.base_system = base_system
        self.max_tokens = max_tokens
        self.docs_text = docs_text
        self.docs_files = docs_files or []
        self.db_enabled = db_enabled and PYODBC_AVAILABLE
        self.messages: list[dict] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.cache_hits = 0
        self.sql_calls = 0

    # ── System blocks ──────────────────────────────────────────────────────────
    def _system_blocks(self) -> list[dict] | str:
        db_note = (
            "\n\nYou have access to the `execute_sql` tool which runs live queries "
            "against the Sealine searates SQL Server database. Use it whenever the "
            "user asks for data, counts, reports, or anything requiring live results."
            if self.db_enabled else ""
        )
        base = self.base_system + db_note

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

    # ── Tool executor ──────────────────────────────────────────────────────────
    def _execute_tool(self, name: str, tool_input: dict) -> str:
        if name == "execute_sql":
            query = tool_input.get("query", "")
            # Show the query being run
            print(c(f"\n  [SQL] ", YELLOW, BOLD), end="")
            short = query.replace("\n", " ").strip()
            print(c(textwrap.shorten(short, width=100, placeholder="…"), DIM))
            result = run_sql(query)
            self.sql_calls += 1
            return result
        return f"Unknown tool: {name}"

    # ── Send (API — returns response text) ──────────────────────────────────
    def send_api(self, user_text: str) -> str:
        """Non-streaming send that returns the full response text. Used by the REST API."""
        self.messages.append({"role": "user", "content": user_text})
        tools = [SQL_TOOL] if self.db_enabled else []

        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self._system_blocks(),
                tools=tools,
                messages=self.messages,
            )

            self.total_input_tokens  += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            if cache_read:
                self.cache_hits += 1

            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result_text = self._execute_tool_silent(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                self.messages.append({"role": "user", "content": tool_results})
                continue

            # Extract text from response
            parts = []
            for block in response.content:
                if block.type == "text":
                    parts.append(block.text)
            return "\n".join(parts)

    def _execute_tool_silent(self, name: str, tool_input: dict) -> str:
        """Execute tool without terminal output (for API use)."""
        if name == "execute_sql":
            query = tool_input.get("query", "")
            result = run_sql(query)
            self.sql_calls += 1
            return result
        return f"Unknown tool: {name}"

    # ── Send (agentic loop with streaming) ────────────────────────────────────
    def send(self, user_text: str) -> None:
        self.messages.append({"role": "user", "content": user_text})
        print(f"\n{c('Claude', CYAN, BOLD)}  ", end="", flush=True)

        tools = [SQL_TOOL] if self.db_enabled else []

        while True:
            collected: list[str] = []

            # Stream one API call
            try:
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
                                print(c("\n[thinking…]", DIM, MAGENTA), flush=True)
                            elif event.content_block.type == "tool_use":
                                # tool name shown after we get the full input
                                pass
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                print(event.delta.text, end="", flush=True)
                                collected.append(event.delta.text)
                    final = stream.get_final_message()

            except anthropic.BadRequestError:
                # Haiku/older model — no thinking support, retry without it
                with self.client.messages.stream(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=self._system_blocks(),
                    tools=tools,
                    messages=self.messages,
                ) as stream:
                    for text in stream.text_stream:
                        print(text, end="", flush=True)
                        collected.append(text)
                    final = stream.get_final_message()

            self.total_input_tokens  += final.usage.input_tokens
            self.total_output_tokens += final.usage.output_tokens
            cache_read = getattr(final.usage, "cache_read_input_tokens", 0) or 0
            if cache_read:
                self.cache_hits += 1

            # Append assistant turn (full content list, preserves tool_use blocks)
            self.messages.append({"role": "assistant", "content": final.content})

            # ── Check if Claude wants to use a tool ───────────────────────────
            if final.stop_reason == "tool_use":
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        result_text = self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                # Feed results back and loop
                self.messages.append({"role": "user", "content": tool_results})
                print(f"\n{c('Claude', CYAN, BOLD)}  ", end="", flush=True)
                continue

            # ── end_turn: done ────────────────────────────────────────────────
            break

        print("\n")

    # ── Slash commands ─────────────────────────────────────────────────────────
    def cmd_clear(self) -> None:
        self.messages.clear()
        print(c("  Conversation cleared.\n", DIM))

    def cmd_history(self) -> None:
        turns = sum(1 for m in self.messages if m["role"] == "user"
                    and not (isinstance(m["content"], list)
                             and m["content"] and m["content"][0].get("type") == "tool_result"))
        print(
            f"\n  {c('Turns:', BOLD)} {turns}  |  "
            f"{c('Input tokens:', BOLD)} {self.total_input_tokens:,}  |  "
            f"{c('Output tokens:', BOLD)} {self.total_output_tokens:,}  |  "
            f"{c('Cache hits:', BOLD)} {self.cache_hits}  |  "
            f"{c('SQL calls:', BOLD)} {self.sql_calls}\n"
        )

    def cmd_docs(self) -> None:
        if not self.docs_files:
            print(c("  No Markdown files loaded.\n", DIM))
            return
        print(f"\n  {c('Loaded Markdown files:', BOLD)}")
        for f in self.docs_files:
            print(f"    {c('•', CYAN)} {f}")
        print()

    def cmd_system(self, rest: str) -> None:
        if rest.strip():
            self.base_system = rest.strip()
            self.messages.clear()
            print(c("  System prompt updated (history cleared).\n", DIM))
        else:
            print(f"\n  {c('System prompt:', BOLD)}\n  {self.base_system}\n")
            if self.docs_files:
                print(c(f"  + {len(self.docs_files)} Markdown file(s) loaded as cached context\n", DIM))

    def cmd_help(self) -> None:
        print(make_banner())


# ── Input helpers ──────────────────────────────────────────────────────────────
def read_input(prompt: str) -> str:
    try:
        first = input(prompt)
    except (EOFError, KeyboardInterrupt):
        raise
    if first == "":
        return ""
    lines = [first]
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()


# ── Main REPL ──────────────────────────────────────────────────────────────────
def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))

    parser = argparse.ArgumentParser(
        description="Terminal chat interface for Claude with live Sealine DB access"
    )
    parser.add_argument("--model", default="claude-haiku-4-5",
                        help="Model ID (default: claude-haiku-4-5)")
    parser.add_argument("--system",
                        default="You are Claude, a helpful AI assistant and data analyst "
                                "for the Sealine shipping database. You have been given "
                                "the database schema and reference documents as context.",
                        help="Base system prompt")
    parser.add_argument("--max-tokens", type=int, default=8192,
                        help="Max output tokens per response (default: 8192)")
    parser.add_argument("--docs-dir", default=script_dir,
                        help=f"Root directory to search for .md files (default: {script_dir})")
    parser.add_argument("--no-docs", action="store_true",
                        help="Skip loading Markdown files")
    parser.add_argument("--no-db", action="store_true",
                        help="Disable live SQL database tool")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(c("Error: ANTHROPIC_API_KEY environment variable is not set.", RED, BOLD))
        sys.exit(1)

    docs_text, docs_files = "", []
    if not args.no_docs:
        docs_text, docs_files = load_md_files(args.docs_dir)

    db_enabled = not args.no_db
    if db_enabled and not PYODBC_AVAILABLE:
        print(c("  Warning: pyodbc not installed — DB tool disabled. Run: pip install pyodbc", YELLOW))
        db_enabled = False

    chat = ClaudeChat(
        model=args.model,
        base_system=args.system,
        max_tokens=args.max_tokens,
        docs_text=docs_text,
        docs_files=docs_files,
        db_enabled=db_enabled,
    )

    print(make_banner())
    print(c(f"  Model    : {chat.model}", DIM))
    print(c(f"  System   : {chat.base_system[:80]}{'…' if len(chat.base_system) > 80 else ''}", DIM))

    if docs_files:
        print(c(f"  Docs     : {len(docs_files)} Markdown file(s) loaded & cached", GREEN))
        for f in docs_files:
            print(c(f"             • {f}", DIM))
    else:
        print(c("  Docs     : none", DIM))

    if chat.db_enabled:
        print(c("  Database : connected — live SQL queries enabled", GREEN))
        print(c("             ushou102-exap1 / searates", DIM))
    else:
        print(c("  Database : disabled (use --no-db to suppress, or install pyodbc)", DIM))

    print()

    user_prompt = f"{c('You', GREEN, BOLD)}  "

    while True:
        try:
            text = read_input(user_prompt)
        except (EOFError, KeyboardInterrupt):
            print(c("\n\n  Goodbye!\n", DIM))
            break

        if not text:
            continue

        if text.startswith("/"):
            cmd, _, rest = text.partition(" ")
            cmd = cmd.lower()
            if cmd in ("/quit", "/exit", "/q"):
                print(c("\n  Goodbye!\n", DIM))
                break
            elif cmd == "/clear":
                chat.cmd_clear()
            elif cmd == "/history":
                chat.cmd_history()
            elif cmd == "/docs":
                chat.cmd_docs()
            elif cmd == "/system":
                chat.cmd_system(rest)
            elif cmd == "/help":
                chat.cmd_help()
            else:
                print(c(f"  Unknown command '{cmd}'. Type /help for help.\n", RED))
            continue

        try:
            chat.send(text)
        except anthropic.AuthenticationError:
            print(c("\n  Error: Invalid API key. Check ANTHROPIC_API_KEY.\n", RED))
        except anthropic.RateLimitError:
            print(c("\n  Error: Rate limited. Please wait and try again.\n", RED))
        except anthropic.APIConnectionError:
            print(c("\n  Error: Network error. Check your internet connection.\n", RED))
        except anthropic.APIStatusError as e:
            print(c(f"\n  API error {e.status_code}: {e.message}\n", RED))


if __name__ == "__main__":
    main()
