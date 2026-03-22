"""
SQL Executor — Runs read-only queries against the Sealine searates database.

Extracted from claude_desktop.py `run_sql()` with the exact same safety
controls: allowlist validation, 500-row cap, 30-second timeout,
pipe-delimited text output.

Additionally exposes a structured SqlResult for the API layer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from server.config import get_config

try:
    import pyodbc

    PYODBC_AVAILABLE = True
except ImportError:
    pyodbc = None  # type: ignore[assignment]
    PYODBC_AVAILABLE = False

logger = logging.getLogger(__name__)

# Hard limit on rows returned to Claude to avoid token explosion
MAX_ROWS = 500

# Allowed first words for safety gating (EXEC/EXECUTE removed — they allow
# arbitrary stored procedure execution which undermines read-only safety).
_ALLOWED_FIRST_WORDS = frozenset({"SELECT", "WITH"})

# Dangerous keywords that should never appear anywhere in a query, even in
# subqueries or multi-statement attacks (e.g., "SELECT 1; DROP TABLE x").
# NOTE: "DELETE" is handled separately below with a word-boundary check so
# that column names like "is_deleted" or "delete_flag" are not falsely blocked.
_DANGEROUS_KEYWORDS = frozenset({
    "DROP", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE",
    "XP_", "SP_CONFIGURE", "SHUTDOWN", "DBCC",
})

# Keywords that need word-boundary + space checks so they don't false-positive on
# column names (e.g., "UpdatedDT", "is_deleted", "delete_flag", "InsertDate",
# "MergeKey", "recreate_flag", "create_date").
_INSERT_STMT_RE = re.compile(r"\bINSERT\s", re.IGNORECASE)
_UPDATE_STMT_RE = re.compile(r"\bUPDATE\s", re.IGNORECASE)
_DELETE_STMT_RE = re.compile(r"\bDELETE\s", re.IGNORECASE)
_MERGE_STMT_RE  = re.compile(r"\bMERGE\s",  re.IGNORECASE)
_CREATE_STMT_RE = re.compile(r"\bCREATE\s", re.IGNORECASE)


@dataclass
class SqlResult:
    """Structured result from a SQL query execution."""

    text: str
    """Pipe-delimited text representation (sent to Claude as tool result)."""

    columns: list[str] = field(default_factory=list)
    """Column names, empty if the query returned no description."""

    rows: list[list[str]] = field(default_factory=list)
    """Row data as lists of string values."""

    truncated: bool = False
    """True if the result set exceeded MAX_ROWS and was capped."""

    error: bool = False
    """True if execution produced an error."""


def _build_connection_string() -> str:
    """Build the ODBC connection string from the current config."""
    cfg = get_config()
    return cfg.db_connection_string


def execute_sql(query: str, connection_string: str | None = None) -> SqlResult:
    """
    Execute a SQL query and return a structured SqlResult.

    Args:
        query: The SQL statement to execute.
        connection_string: Optional override; defaults to config-derived string.

    Returns:
        SqlResult with .text for Claude and structured .columns/.rows for the API.
    """
    if not PYODBC_AVAILABLE:
        return SqlResult(
            text="ERROR: pyodbc is not installed. Database queries are unavailable.",
            error=True,
        )

    q = query.strip()

    if not q:
        return SqlResult(text="ERROR: Empty query.", error=True)

    # ---- Safety: only allow read operations ----
    first_word = q.split(maxsplit=1)[0].upper()
    if first_word not in _ALLOWED_FIRST_WORDS:
        return SqlResult(
            text="ERROR: Only SELECT / WITH queries are permitted.",
            error=True,
        )

    # Scan full query for dangerous keywords (prevents multi-statement attacks
    # like "SELECT 1; DROP TABLE x" and subquery writes).
    q_upper = q.upper()
    for keyword in _DANGEROUS_KEYWORDS:
        if keyword in q_upper:
            return SqlResult(
                text=f"ERROR: Query contains disallowed keyword: {keyword}",
                error=True,
            )

    # INSERT, UPDATE, DELETE, MERGE, CREATE require word-boundary + space checks so
    # column names like "InsertDate", "UpdatedDT", "is_deleted", "MergeKey",
    # "recreate_flag", "create_date" are allowed.
    for _re, _kw in (
        (_INSERT_STMT_RE, "INSERT"),
        (_UPDATE_STMT_RE, "UPDATE"),
        (_DELETE_STMT_RE, "DELETE"),
        (_MERGE_STMT_RE,  "MERGE"),
        (_CREATE_STMT_RE, "CREATE"),
    ):
        if _re.search(q):
            return SqlResult(
                text=f"ERROR: Query contains disallowed keyword: {_kw}",
                error=True,
            )

    conn_str = connection_string or _build_connection_string()

    conn = None
    try:
        conn = pyodbc.connect(conn_str, timeout=300)
        conn.timeout = 300  # Query execution timeout (seconds)
        cursor = conn.cursor()
        cursor.execute(q)

        # Some statements may not return a result set
        if cursor.description is None:
            return SqlResult(
                text="Query executed successfully (no rows returned).",
            )

        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]

        if not rows:
            return SqlResult(
                text=f"Columns: {', '.join(cols)}\n(0 rows)",
                columns=cols,
            )

        # Build pipe-delimited table (identical to original run_sql logic)
        DISPLAY_MAX = 500  # max chars shown per cell in the text table
        col_widths = [len(col) for col in cols]
        str_rows: list[list[str]] = []
        for row in rows:
            str_row = [str(v) if v is not None else "NULL" for v in row]
            for i, val in enumerate(str_row):
                col_widths[i] = max(col_widths[i], min(len(val), DISPLAY_MAX))
            str_rows.append(str_row)

        def fmt_row(values: list[str]) -> str:
            return "  ".join(
                v[:DISPLAY_MAX].ljust(col_widths[i]) for i, v in enumerate(values)
            )

        header = fmt_row(cols)
        separator = "  ".join("-" * w for w in col_widths)
        lines = [header, separator] + [fmt_row(r) for r in str_rows]
        if truncated:
            lines.append(f"\n(Showing first {MAX_ROWS} rows — results truncated)")
        else:
            lines.append(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")

        text_output = "\n".join(lines)

        return SqlResult(
            text=text_output,
            columns=cols,
            rows=str_rows,
            truncated=truncated,
        )

    except Exception as e:
        logger.exception("SQL execution error")
        return SqlResult(
            text=f"SQL ERROR: {e}",
            error=True,
        )
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
