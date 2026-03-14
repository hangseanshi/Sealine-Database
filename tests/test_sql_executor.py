"""
Unit tests for server.core.sql_executor module.

Tests cover:
  - Allowed queries (SELECT, WITH, EXEC, EXECUTE)
  - Blocked queries (DROP, DELETE, INSERT, UPDATE, ALTER, TRUNCATE)
  - Timeout configuration (30 seconds)
  - Truncation at MAX_ROWS (500)
  - Error handling (connection failures, query errors)
  - Connection string building
  - SqlResult dataclass structure
  - Empty query handling
  - pyodbc unavailability
  - Pipe-delimited text output format
"""

from __future__ import annotations

import sys
from dataclasses import fields
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# We need to mock pyodbc before importing sql_executor, since
# the module does a conditional import at load time.
# For our tests, we want to control PYODBC_AVAILABLE explicitly.


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_config_singleton():
    """Reset the config singleton before each test."""
    import server.config as config_module
    config_module._config = None
    yield
    config_module._config = None


@pytest.fixture
def mock_pyodbc():
    """Provide a mocked pyodbc module and ensure PYODBC_AVAILABLE is True."""
    mock_module = MagicMock()
    with patch.dict(sys.modules, {"pyodbc": mock_module}):
        import server.core.sql_executor as sql_mod
        original_flag = sql_mod.PYODBC_AVAILABLE
        original_pyodbc = sql_mod.pyodbc
        sql_mod.PYODBC_AVAILABLE = True
        sql_mod.pyodbc = mock_module
        yield mock_module, sql_mod
        sql_mod.PYODBC_AVAILABLE = original_flag
        sql_mod.pyodbc = original_pyodbc


@pytest.fixture
def sql_mod_no_pyodbc():
    """Provide the sql_executor module with PYODBC_AVAILABLE = False."""
    import server.core.sql_executor as sql_mod
    original_flag = sql_mod.PYODBC_AVAILABLE
    sql_mod.PYODBC_AVAILABLE = False
    yield sql_mod
    sql_mod.PYODBC_AVAILABLE = original_flag


def _setup_cursor(mock_pyodbc_module, columns, rows, description=None):
    """Configure mock pyodbc to return specific columns and rows."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    if description is None and columns:
        description = [(col,) for col in columns]
    mock_cursor.description = description
    mock_cursor.fetchmany.return_value = rows

    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc_module.connect.return_value = mock_conn

    return mock_conn, mock_cursor


# ---------------------------------------------------------------------------
# Tests: Allowed queries
# ---------------------------------------------------------------------------


class TestAllowedQueries:
    """Verify that SELECT, WITH, EXEC, EXECUTE are accepted."""

    def test_select_query_allowed(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id", "name"], [("1", "Alice")])
        result = sql_mod.execute_sql("SELECT * FROM users", connection_string="fake")
        assert not result.error
        assert result.columns == ["id", "name"]

    def test_select_lowercase_allowed(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        result = sql_mod.execute_sql("select * from users", connection_string="fake")
        assert not result.error

    def test_select_mixed_case_allowed(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        result = sql_mod.execute_sql("Select * FROM users", connection_string="fake")
        assert not result.error

    def test_with_query_allowed(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["cnt"], [("42",)])
        result = sql_mod.execute_sql(
            "WITH cte AS (SELECT 1 AS cnt) SELECT * FROM cte",
            connection_string="fake",
        )
        assert not result.error

    def test_exec_query_blocked(self, mock_pyodbc):
        """EXEC/EXECUTE are no longer allowed (security fix)."""
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, [], [], description=None)
        result = sql_mod.execute_sql("EXEC sp_help", connection_string="fake")
        assert result.error

    def test_execute_query_blocked(self, mock_pyodbc):
        """EXEC/EXECUTE are no longer allowed (security fix)."""
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, [], [], description=None)
        result = sql_mod.execute_sql("EXECUTE sp_help", connection_string="fake")
        assert result.error

    def test_select_with_leading_whitespace(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        result = sql_mod.execute_sql("   SELECT * FROM users", connection_string="fake")
        assert not result.error


# ---------------------------------------------------------------------------
# Tests: Blocked queries
# ---------------------------------------------------------------------------


class TestBlockedQueries:
    """Verify that non-read operations are blocked."""

    @pytest.mark.parametrize("query", [
        "DROP TABLE users",
        "DELETE FROM users WHERE id = 1",
        "INSERT INTO users (name) VALUES ('Bob')",
        "UPDATE users SET name = 'Bob' WHERE id = 1",
        "ALTER TABLE users ADD COLUMN age INT",
        "TRUNCATE TABLE users",
        "CREATE TABLE new_table (id INT)",
        "GRANT ALL ON users TO public",
    ])
    def test_blocked_query_returns_error(self, mock_pyodbc, query):
        mock_mod, sql_mod = mock_pyodbc
        result = sql_mod.execute_sql(query, connection_string="fake")
        assert result.error
        assert "Only SELECT" in result.text or "only" in result.text.lower()

    def test_drop_lowercase_blocked(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        result = sql_mod.execute_sql("drop table users", connection_string="fake")
        assert result.error

    def test_empty_query_blocked(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        result = sql_mod.execute_sql("", connection_string="fake")
        assert result.error

    def test_whitespace_only_query_blocked(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        result = sql_mod.execute_sql("   ", connection_string="fake")
        assert result.error


# ---------------------------------------------------------------------------
# Tests: Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    """Verify that the connection uses a 30-second timeout."""

    def test_connect_called_with_timeout_30(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        sql_mod.execute_sql("SELECT 1", connection_string="fake_conn_str")
        mock_mod.connect.assert_called_once_with("fake_conn_str", timeout=30)


# ---------------------------------------------------------------------------
# Tests: Truncation at 500 rows
# ---------------------------------------------------------------------------


class TestTruncation:
    """Verify row limit enforcement at MAX_ROWS (500)."""

    def test_result_not_truncated_under_limit(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        rows = [(str(i),) for i in range(100)]
        _setup_cursor(mock_mod, ["id"], rows)
        result = sql_mod.execute_sql("SELECT id FROM t", connection_string="fake")
        assert not result.truncated
        assert len(result.rows) == 100

    def test_result_truncated_at_limit(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        # Return 501 rows to trigger truncation
        rows = [(str(i),) for i in range(501)]
        _setup_cursor(mock_mod, ["id"], rows)
        result = sql_mod.execute_sql("SELECT id FROM t", connection_string="fake")
        assert result.truncated
        assert len(result.rows) == 500

    def test_truncation_message_in_text(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        rows = [(str(i),) for i in range(501)]
        _setup_cursor(mock_mod, ["id"], rows)
        result = sql_mod.execute_sql("SELECT id FROM t", connection_string="fake")
        assert "500" in result.text
        assert "truncated" in result.text.lower()

    def test_exactly_500_rows_not_truncated(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        rows = [(str(i),) for i in range(500)]
        _setup_cursor(mock_mod, ["id"], rows)
        result = sql_mod.execute_sql("SELECT id FROM t", connection_string="fake")
        assert not result.truncated
        assert len(result.rows) == 500

    def test_fetchmany_called_with_501(self, mock_pyodbc):
        """fetchmany should be called with MAX_ROWS + 1 to detect truncation."""
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        sql_mod.execute_sql("SELECT id FROM t", connection_string="fake")
        mock_conn = mock_mod.connect.return_value
        mock_cursor = mock_conn.cursor.return_value
        mock_cursor.fetchmany.assert_called_once_with(501)


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Verify error handling for various failure scenarios."""

    def test_connection_error_returns_sql_error(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        mock_mod.connect.side_effect = Exception("Connection refused")
        result = sql_mod.execute_sql("SELECT 1", connection_string="fake")
        assert result.error
        assert "SQL ERROR" in result.text

    def test_cursor_execute_error(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Syntax error in SQL")
        mock_conn.cursor.return_value = mock_cursor
        mock_mod.connect.return_value = mock_conn
        result = sql_mod.execute_sql("SELECT bad syntax", connection_string="fake")
        assert result.error
        assert "SQL ERROR" in result.text

    def test_pyodbc_not_available(self, sql_mod_no_pyodbc):
        result = sql_mod_no_pyodbc.execute_sql("SELECT 1")
        assert result.error
        assert "pyodbc is not installed" in result.text

    def test_no_description_returns_success_message(self, mock_pyodbc):
        """SELECT that returns no description should get a success message."""
        mock_mod, sql_mod = mock_pyodbc
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_conn.cursor.return_value = mock_cursor
        mock_mod.connect.return_value = mock_conn
        result = sql_mod.execute_sql("SELECT 1", connection_string="fake")
        assert not result.error
        assert "no rows returned" in result.text.lower()

    def test_empty_result_set(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id", "name"], [])
        result = sql_mod.execute_sql("SELECT * FROM empty_table", connection_string="fake")
        assert not result.error
        assert "0 rows" in result.text
        assert result.columns == ["id", "name"]
        assert len(result.rows) == 0


# ---------------------------------------------------------------------------
# Tests: Connection string building
# ---------------------------------------------------------------------------


class TestConnectionStringBuilding:
    """Verify connection string is built from config when not provided."""

    def test_uses_provided_connection_string(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        sql_mod.execute_sql("SELECT 1", connection_string="my_custom_conn_str")
        mock_mod.connect.assert_called_once_with("my_custom_conn_str", timeout=30)

    def test_builds_connection_string_from_config(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])

        with patch("server.core.sql_executor.get_config") as mock_get_config:
            mock_cfg = MagicMock()
            mock_cfg.db_connection_string = "DRIVER={ODBC};SERVER=test;"
            mock_get_config.return_value = mock_cfg
            sql_mod.execute_sql("SELECT 1")
            mock_mod.connect.assert_called_once_with(
                "DRIVER={ODBC};SERVER=test;", timeout=30
            )


# ---------------------------------------------------------------------------
# Tests: SqlResult dataclass
# ---------------------------------------------------------------------------


class TestSqlResult:
    """Verify SqlResult dataclass structure and defaults."""

    def test_default_values(self):
        from server.core.sql_executor import SqlResult
        result = SqlResult(text="test")
        assert result.text == "test"
        assert result.columns == []
        assert result.rows == []
        assert result.truncated is False
        assert result.error is False

    def test_custom_values(self):
        from server.core.sql_executor import SqlResult
        result = SqlResult(
            text="output",
            columns=["a", "b"],
            rows=[["1", "2"]],
            truncated=True,
            error=False,
        )
        assert result.columns == ["a", "b"]
        assert result.rows == [["1", "2"]]
        assert result.truncated is True

    def test_has_expected_fields(self):
        from server.core.sql_executor import SqlResult
        field_names = {f.name for f in fields(SqlResult)}
        assert "text" in field_names
        assert "columns" in field_names
        assert "rows" in field_names
        assert "truncated" in field_names
        assert "error" in field_names


# ---------------------------------------------------------------------------
# Tests: Output format
# ---------------------------------------------------------------------------


class TestOutputFormat:
    """Verify the pipe-delimited text output format."""

    def test_output_contains_column_headers(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id", "name"], [("1", "Alice")])
        result = sql_mod.execute_sql("SELECT id, name FROM users", connection_string="fake")
        assert "id" in result.text
        assert "name" in result.text

    def test_output_contains_data_values(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id", "name"], [("1", "Alice")])
        result = sql_mod.execute_sql("SELECT id, name FROM users", connection_string="fake")
        assert "1" in result.text
        assert "Alice" in result.text

    def test_output_contains_separator_line(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        result = sql_mod.execute_sql("SELECT id FROM t", connection_string="fake")
        lines = result.text.split("\n")
        # Second line should be separator dashes
        assert all(c in "-  " for c in lines[1])

    def test_row_count_in_output(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",), ("2",), ("3",)])
        result = sql_mod.execute_sql("SELECT id FROM t", connection_string="fake")
        assert "3 rows" in result.text

    def test_single_row_says_row_not_rows(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        result = sql_mod.execute_sql("SELECT id FROM t", connection_string="fake")
        assert "1 row)" in result.text
        # Should NOT say "1 rows"

    def test_null_values_displayed_as_null(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id", "name"], [(1, None)])
        result = sql_mod.execute_sql("SELECT id, name FROM t", connection_string="fake")
        assert "NULL" in result.text
        null_found = any("NULL" in r for r in result.rows[0])
        assert null_found

    def test_connection_closed_after_query(self, mock_pyodbc):
        mock_mod, sql_mod = mock_pyodbc
        _setup_cursor(mock_mod, ["id"], [("1",)])
        sql_mod.execute_sql("SELECT 1", connection_string="fake")
        mock_conn = mock_mod.connect.return_value
        mock_conn.close.assert_called()


# ---------------------------------------------------------------------------
# Tests: MAX_ROWS constant
# ---------------------------------------------------------------------------


class TestMaxRowsConstant:
    """Verify the MAX_ROWS constant value."""

    def test_max_rows_is_500(self):
        from server.core.sql_executor import MAX_ROWS
        assert MAX_ROWS == 500


# ---------------------------------------------------------------------------
# Tests: Allowed first words constant
# ---------------------------------------------------------------------------


class TestAllowedFirstWords:
    """Verify the allowed first words set."""

    def test_allowed_words(self):
        from server.core.sql_executor import _ALLOWED_FIRST_WORDS
        assert "SELECT" in _ALLOWED_FIRST_WORDS
        assert "WITH" in _ALLOWED_FIRST_WORDS
        # EXEC/EXECUTE removed for security (arbitrary stored proc execution)
        assert "EXEC" not in _ALLOWED_FIRST_WORDS
        assert "EXECUTE" not in _ALLOWED_FIRST_WORDS
        # Should NOT contain dangerous words
        assert "DROP" not in _ALLOWED_FIRST_WORDS
        assert "DELETE" not in _ALLOWED_FIRST_WORDS
        assert "INSERT" not in _ALLOWED_FIRST_WORDS
        assert "UPDATE" not in _ALLOWED_FIRST_WORDS
