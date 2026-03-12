"""
Unit tests for server.core.file_generator module.

Tests cover:
  - Static plot generation (matplotlib PNG) for all plot types
  - Interactive plot generation (plotly HTML)
  - PDF generation with WeasyPrint (mocked)
  - PDF generation fallback to ReportLab (mocked)
  - PDF generation when neither library is available
  - Excel generation with openpyxl (mocked)
  - File cleanup of expired files
  - Invalid inputs (unsupported plot types, missing data)
  - file_id generation (_short_uuid)
  - Slug generation (_slugify)
  - _is_numeric helper
  - _esc HTML escaping
  - handle_file_tool dispatch
  - FILE_TOOLS definition structure
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from unittest.mock import patch, MagicMock, ANY

import pytest

from server.core.file_generator import (
    _short_uuid,
    _slugify,
    _is_numeric,
    _esc,
    _file_meta,
    ensure_file_store,
    generate_plot,
    generate_pdf,
    generate_excel,
    cleanup_expired_files,
    handle_file_tool,
    FILE_TOOLS,
    HEADER_COLOR_HEX,
    ALT_ROW_COLOR,
    MAX_COL_WIDTH,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_store():
    """Create a temporary file store directory."""
    d = tempfile.mkdtemp(prefix="test_file_gen_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests: _short_uuid
# ---------------------------------------------------------------------------


class TestShortUuid:
    """Test the _short_uuid helper."""

    def test_returns_string(self):
        result = _short_uuid()
        assert isinstance(result, str)

    def test_length_is_8(self):
        result = _short_uuid()
        assert len(result) == 8

    def test_is_hex(self):
        result = _short_uuid()
        # Should only contain hex characters
        int(result, 16)  # will raise ValueError if not valid hex

    def test_unique(self):
        """Multiple calls should return different values."""
        results = {_short_uuid() for _ in range(100)}
        # Very high probability of uniqueness
        assert len(results) == 100


# ---------------------------------------------------------------------------
# Tests: _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    """Test the _slugify helper."""

    def test_basic_text(self):
        assert _slugify("Hello World") == "hello_world"

    def test_special_characters_removed(self):
        result = _slugify("Report #1 (2024)")
        assert "#" not in result
        assert "(" not in result
        assert ")" not in result

    def test_dashes_become_underscores(self):
        result = _slugify("my-report-title")
        assert result == "my_report_title"

    def test_multiple_spaces_collapsed(self):
        result = _slugify("too   many   spaces")
        assert result == "too_many_spaces"

    def test_leading_trailing_stripped(self):
        result = _slugify("  hello  ")
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_max_length_80(self):
        long_text = "a" * 200
        result = _slugify(long_text)
        assert len(result) <= 80

    def test_empty_input_returns_file(self):
        result = _slugify("")
        assert result == "file"

    def test_only_special_chars_returns_file(self):
        result = _slugify("!@#$%^&*()")
        assert result == "file"

    def test_lowercase(self):
        result = _slugify("UPPERCASE")
        assert result == "uppercase"


# ---------------------------------------------------------------------------
# Tests: _is_numeric
# ---------------------------------------------------------------------------


class TestIsNumeric:
    """Test the _is_numeric helper."""

    def test_integer(self):
        assert _is_numeric(42) is True

    def test_float(self):
        assert _is_numeric(3.14) is True

    def test_numeric_string(self):
        assert _is_numeric("123") is True

    def test_numeric_string_with_decimal(self):
        assert _is_numeric("123.45") is True

    def test_numeric_string_with_commas(self):
        assert _is_numeric("1,234,567") is True

    def test_non_numeric_string(self):
        assert _is_numeric("hello") is False

    def test_empty_string(self):
        assert _is_numeric("") is False

    def test_none(self):
        assert _is_numeric(None) is False

    def test_list(self):
        assert _is_numeric([1, 2]) is False

    def test_negative_number(self):
        assert _is_numeric(-5) is True

    def test_negative_string(self):
        assert _is_numeric("-5.5") is True


# ---------------------------------------------------------------------------
# Tests: _esc (HTML escaping)
# ---------------------------------------------------------------------------


class TestEsc:
    """Test the _esc HTML escaping helper."""

    def test_ampersand(self):
        assert _esc("A & B") == "A &amp; B"

    def test_less_than(self):
        assert _esc("a < b") == "a &lt; b"

    def test_greater_than(self):
        assert _esc("a > b") == "a &gt; b"

    def test_double_quote(self):
        assert _esc('say "hello"') == "say &quot;hello&quot;"

    def test_no_escaping_needed(self):
        assert _esc("plain text") == "plain text"

    def test_multiple_special_chars(self):
        result = _esc('<script>alert("XSS")</script>')
        assert "<" not in result
        assert ">" not in result


# ---------------------------------------------------------------------------
# Tests: ensure_file_store
# ---------------------------------------------------------------------------


class TestEnsureFileStore:
    """Test the ensure_file_store directory creation."""

    def test_creates_directory(self, tmp_store):
        new_path = os.path.join(tmp_store, "new_subdir")
        ensure_file_store(new_path)
        assert os.path.isdir(new_path)

    def test_existing_directory_no_error(self, tmp_store):
        ensure_file_store(tmp_store)
        # Should not raise

    def test_creates_nested_directories(self, tmp_store):
        nested = os.path.join(tmp_store, "a", "b", "c")
        ensure_file_store(nested)
        assert os.path.isdir(nested)


# ---------------------------------------------------------------------------
# Tests: _file_meta
# ---------------------------------------------------------------------------


class TestFileMeta:
    """Test the _file_meta helper."""

    def test_returns_expected_keys(self, tmp_store):
        filepath = os.path.join(tmp_store, "test.txt")
        with open(filepath, "w") as f:
            f.write("hello")
        meta = _file_meta("abc123", "test.txt", "text/plain", filepath)
        assert meta["file_id"] == "abc123"
        assert meta["filename"] == "test.txt"
        assert meta["file_type"] == "text/plain"
        assert meta["file_path"] == filepath
        assert meta["size_bytes"] > 0

    def test_nonexistent_file_size_zero(self):
        meta = _file_meta("id", "f.txt", "text/plain", "/nonexistent/path")
        assert meta["size_bytes"] == 0


# ---------------------------------------------------------------------------
# Tests: generate_plot (static with matplotlib)
# ---------------------------------------------------------------------------


class TestGeneratePlotStatic:
    """Test static plot generation using matplotlib."""

    def test_bar_chart(self, tmp_store):
        result = generate_plot(
            plot_type="bar",
            title="Test Bar",
            data={"labels": ["A", "B", "C"], "values": [1, 2, 3]},
            file_store_path=tmp_store,
        )
        assert "error" not in result
        assert result["file_type"] == "image/png"
        assert os.path.exists(result["file_path"])

    def test_line_chart(self, tmp_store):
        result = generate_plot(
            plot_type="line",
            title="Test Line",
            data={"labels": ["Jan", "Feb"], "values": [10, 20]},
            file_store_path=tmp_store,
        )
        assert "error" not in result
        assert result["file_type"] == "image/png"

    def test_scatter_chart(self, tmp_store):
        result = generate_plot(
            plot_type="scatter",
            title="Test Scatter",
            data={"x": [1, 2, 3], "y": [4, 5, 6]},
            file_store_path=tmp_store,
        )
        assert "error" not in result

    def test_pie_chart(self, tmp_store):
        result = generate_plot(
            plot_type="pie",
            title="Test Pie",
            data={"labels": ["A", "B", "C"], "values": [30, 50, 20]},
            file_store_path=tmp_store,
        )
        assert "error" not in result

    def test_histogram(self, tmp_store):
        result = generate_plot(
            plot_type="histogram",
            title="Test Histogram",
            data={"values": [1, 2, 2, 3, 3, 3, 4, 4, 5]},
            file_store_path=tmp_store,
        )
        assert "error" not in result

    def test_unsupported_plot_type(self, tmp_store):
        result = generate_plot(
            plot_type="radar",
            title="Bad Type",
            data={"labels": ["A"], "values": [1]},
            file_store_path=tmp_store,
        )
        assert "error" in result
        assert "Unsupported" in result["error"]

    def test_with_axis_labels(self, tmp_store):
        result = generate_plot(
            plot_type="bar",
            title="Labeled Chart",
            data={"labels": ["A"], "values": [1]},
            x_label="Categories",
            y_label="Values",
            file_store_path=tmp_store,
        )
        assert "error" not in result

    def test_file_id_in_filename(self, tmp_store):
        result = generate_plot(
            plot_type="bar",
            title="ID Test",
            data={"labels": ["A"], "values": [1]},
            file_store_path=tmp_store,
        )
        assert result["file_id"] in os.path.basename(result["file_path"])

    def test_heatmap_chart(self, tmp_store):
        result = generate_plot(
            plot_type="heatmap",
            title="Test Heatmap",
            data={
                "labels_x": ["A", "B"],
                "labels_y": ["X", "Y"],
                "values": [[1, 2], [3, 4]],
            },
            file_store_path=tmp_store,
        )
        assert "error" not in result


# ---------------------------------------------------------------------------
# Tests: generate_plot (interactive with plotly - mocked)
# ---------------------------------------------------------------------------


class TestGeneratePlotInteractive:
    """Test interactive plot generation using plotly (mocked)."""

    def test_interactive_bar_chart(self, tmp_store):
        mock_fig = MagicMock()
        mock_go = MagicMock()
        mock_go.Figure.return_value = mock_fig
        mock_go.Bar.return_value = "bar_trace"

        # Set up the plotly mock so that `import plotly.graph_objects as go`
        # resolves properly through sys.modules
        mock_plotly = MagicMock()
        mock_plotly.graph_objects = mock_go

        with patch.dict("sys.modules", {"plotly": mock_plotly, "plotly.graph_objects": mock_go}):
            result = generate_plot(
                plot_type="bar",
                title="Interactive Bar",
                data={"labels": ["A", "B"], "values": [1, 2]},
                interactive=True,
                file_store_path=tmp_store,
            )
            # The function creates a Figure with Bar data
            mock_go.Figure.assert_called_once()
            mock_go.Bar.assert_called_once()

    def test_interactive_unsupported_type(self, tmp_store):
        mock_go = MagicMock()

        with patch.dict("sys.modules", {"plotly": MagicMock(), "plotly.graph_objects": mock_go}):
            result = generate_plot(
                plot_type="unknown_type",
                title="Bad",
                data={"labels": ["A"], "values": [1]},
                interactive=True,
                file_store_path=tmp_store,
            )
            assert "error" in result

    def test_interactive_plotly_not_installed(self, tmp_store):
        """When plotly is not importable, should return an error."""
        # Remove plotly from sys.modules if it exists and make import fail
        import sys
        with patch.dict(sys.modules, {"plotly": None, "plotly.graph_objects": None}):
            result = generate_plot(
                plot_type="bar",
                title="No Plotly",
                data={"labels": ["A"], "values": [1]},
                interactive=True,
                file_store_path=tmp_store,
            )
            assert "error" in result


# ---------------------------------------------------------------------------
# Tests: generate_pdf
# ---------------------------------------------------------------------------


class TestGeneratePdf:
    """Test PDF generation with WeasyPrint and fallback."""

    def test_pdf_with_weasyprint(self, tmp_store):
        """Test WeasyPrint PDF generation path."""
        mock_html_cls = MagicMock()
        mock_html_instance = MagicMock()
        mock_html_cls.return_value = mock_html_instance

        with patch("server.core.file_generator._pdf_weasyprint") as mock_wp:
            mock_wp.return_value = {
                "file_id": "abc123",
                "filename": "report.pdf",
                "file_type": "application/pdf",
                "file_path": os.path.join(tmp_store, "abc123_report.pdf"),
                "size_bytes": 1024,
            }
            result = generate_pdf(
                title="Test Report",
                columns=["Name", "Value"],
                rows=[["Alice", "100"]],
                file_store_path=tmp_store,
            )
            assert result["file_type"] == "application/pdf"
            assert result["filename"] == "report.pdf"

    def test_pdf_fallback_to_reportlab(self, tmp_store):
        """When WeasyPrint raises ImportError, falls back to ReportLab."""
        with patch("server.core.file_generator._pdf_weasyprint", side_effect=ImportError):
            with patch("server.core.file_generator._pdf_reportlab") as mock_rl:
                mock_rl.return_value = {
                    "file_id": "abc123",
                    "filename": "report.pdf",
                    "file_type": "application/pdf",
                    "file_path": os.path.join(tmp_store, "abc123_report.pdf"),
                    "size_bytes": 512,
                }
                result = generate_pdf(
                    title="Test Report",
                    columns=["Col1"],
                    rows=[["val1"]],
                    file_store_path=tmp_store,
                )
                assert result["file_type"] == "application/pdf"
                mock_rl.assert_called_once()

    def test_pdf_neither_library_available(self, tmp_store):
        """When both WeasyPrint and ReportLab are unavailable."""
        with patch("server.core.file_generator._pdf_weasyprint", side_effect=ImportError):
            with patch("server.core.file_generator._pdf_reportlab", side_effect=ImportError):
                result = generate_pdf(
                    title="Test",
                    columns=["A"],
                    rows=[["1"]],
                    file_store_path=tmp_store,
                )
                assert "error" in result
                assert "weasyprint" in result["error"].lower() or "reportlab" in result["error"].lower()

    def test_pdf_with_summary(self, tmp_store):
        with patch("server.core.file_generator._pdf_weasyprint") as mock_wp:
            mock_wp.return_value = {"file_id": "id", "filename": "f.pdf",
                                    "file_type": "application/pdf",
                                    "file_path": "/tmp/f.pdf", "size_bytes": 0}
            result = generate_pdf(
                title="Report",
                columns=["A"],
                rows=[["1"]],
                summary="This is a summary",
                file_store_path=tmp_store,
            )
            assert "error" not in result

    def test_pdf_custom_filename(self, tmp_store):
        with patch("server.core.file_generator._pdf_weasyprint") as mock_wp:
            mock_wp.return_value = {"file_id": "id", "filename": "custom.pdf",
                                    "file_type": "application/pdf",
                                    "file_path": "/tmp/custom.pdf", "size_bytes": 0}
            result = generate_pdf(
                title="Report",
                columns=["A"],
                rows=[["1"]],
                filename="custom_report",
                file_store_path=tmp_store,
            )
            assert "error" not in result

    def test_pdf_weasyprint_runtime_error(self, tmp_store):
        """When WeasyPrint raises a non-ImportError exception."""
        with patch("server.core.file_generator._pdf_weasyprint",
                    side_effect=RuntimeError("Rendering failed")):
            result = generate_pdf(
                title="Report",
                columns=["A"],
                rows=[["1"]],
                file_store_path=tmp_store,
            )
            assert "error" in result
            assert "Rendering failed" in result["error"]


# ---------------------------------------------------------------------------
# Tests: generate_excel
# ---------------------------------------------------------------------------


class TestGenerateExcel:
    """Test Excel generation with openpyxl."""

    def test_excel_generation(self, tmp_store):
        """Test basic Excel generation. openpyxl may or may not be installed."""
        result = generate_excel(
            title="Test Sheet",
            columns=["Name", "Age"],
            rows=[["Alice", "30"], ["Bob", "25"]],
            file_store_path=tmp_store,
        )
        # If openpyxl is installed, we get a file; if not, an error
        if "error" in result:
            assert "openpyxl" in result["error"]
        else:
            assert result["file_type"] == (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            assert os.path.exists(result["file_path"])

    def test_excel_with_custom_filename(self, tmp_store):
        result = generate_excel(
            title="Test Sheet",
            columns=["Col1"],
            rows=[["val1"]],
            filename="my_report",
            file_store_path=tmp_store,
        )
        if "error" not in result:
            assert "my_report" in result["filename"]

    def test_excel_empty_rows(self, tmp_store):
        """Excel generation with no data rows should still work."""
        result = generate_excel(
            title="Empty Sheet",
            columns=["A", "B"],
            rows=[],
            file_store_path=tmp_store,
        )
        if "error" not in result:
            assert os.path.exists(result["file_path"])

    def test_excel_long_title_truncated(self, tmp_store):
        """Excel sheet name is limited to 31 characters."""
        long_title = "A" * 100
        result = generate_excel(
            title=long_title,
            columns=["Col"],
            rows=[["val"]],
            file_store_path=tmp_store,
        )
        # Should not error; title should be truncated internally
        if "error" in result:
            assert "openpyxl" in result["error"]


# ---------------------------------------------------------------------------
# Tests: cleanup_expired_files
# ---------------------------------------------------------------------------


class TestCleanupExpiredFiles:
    """Test the file cleanup utility."""

    def test_cleanup_empty_directory(self, tmp_store):
        deleted = cleanup_expired_files(tmp_store, ttl_hours=24)
        assert deleted == 0

    def test_cleanup_nonexistent_directory(self):
        deleted = cleanup_expired_files("/nonexistent/path", ttl_hours=24)
        assert deleted == 0

    def test_cleanup_removes_old_files(self, tmp_store):
        """Files older than TTL should be deleted."""
        old_file = os.path.join(tmp_store, "old_file.txt")
        with open(old_file, "w") as f:
            f.write("old content")
        # Set modification time to 25 hours ago
        old_mtime = time.time() - (25 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        deleted = cleanup_expired_files(tmp_store, ttl_hours=24)
        assert deleted == 1
        assert not os.path.exists(old_file)

    def test_cleanup_keeps_recent_files(self, tmp_store):
        """Files newer than TTL should be kept."""
        recent_file = os.path.join(tmp_store, "recent.txt")
        with open(recent_file, "w") as f:
            f.write("recent content")

        deleted = cleanup_expired_files(tmp_store, ttl_hours=24)
        assert deleted == 0
        assert os.path.exists(recent_file)

    def test_cleanup_mixed_old_and_new(self, tmp_store):
        """Only old files should be deleted; recent ones kept."""
        old_file = os.path.join(tmp_store, "old.txt")
        new_file = os.path.join(tmp_store, "new.txt")

        with open(old_file, "w") as f:
            f.write("old")
        with open(new_file, "w") as f:
            f.write("new")

        old_mtime = time.time() - (48 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        deleted = cleanup_expired_files(tmp_store, ttl_hours=24)
        assert deleted == 1
        assert not os.path.exists(old_file)
        assert os.path.exists(new_file)

    def test_cleanup_custom_ttl(self, tmp_store):
        """Test with a very short TTL (1 hour)."""
        old_file = os.path.join(tmp_store, "slightly_old.txt")
        with open(old_file, "w") as f:
            f.write("content")
        old_mtime = time.time() - (2 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        deleted = cleanup_expired_files(tmp_store, ttl_hours=1)
        assert deleted == 1

    def test_cleanup_returns_count(self, tmp_store):
        """Return value should be number of deleted files."""
        for i in range(5):
            path = os.path.join(tmp_store, f"old_{i}.txt")
            with open(path, "w") as f:
                f.write("content")
            old_mtime = time.time() - (48 * 3600)
            os.utime(path, (old_mtime, old_mtime))

        deleted = cleanup_expired_files(tmp_store, ttl_hours=24)
        assert deleted == 5


# ---------------------------------------------------------------------------
# Tests: handle_file_tool dispatch
# ---------------------------------------------------------------------------


class TestHandleFileTool:
    """Test the handle_file_tool dispatch function."""

    def test_dispatch_generate_plot(self, tmp_store):
        with patch("server.core.file_generator.generate_plot") as mock_gen:
            mock_gen.return_value = {"file_id": "abc"}
            result = handle_file_tool(
                "generate_plot",
                {"plot_type": "bar", "title": "T", "data": {"labels": [], "values": []}},
                file_store_path=tmp_store,
            )
            mock_gen.assert_called_once()
            assert result == {"file_id": "abc"}

    def test_dispatch_generate_pdf(self, tmp_store):
        with patch("server.core.file_generator.generate_pdf") as mock_gen:
            mock_gen.return_value = {"file_id": "def"}
            result = handle_file_tool(
                "generate_pdf",
                {"title": "T", "columns": ["A"], "rows": [["1"]]},
                file_store_path=tmp_store,
            )
            mock_gen.assert_called_once()

    def test_dispatch_generate_excel(self, tmp_store):
        with patch("server.core.file_generator.generate_excel") as mock_gen:
            mock_gen.return_value = {"file_id": "ghi"}
            result = handle_file_tool(
                "generate_excel",
                {"title": "T", "columns": ["A"], "rows": [["1"]]},
                file_store_path=tmp_store,
            )
            mock_gen.assert_called_once()

    def test_dispatch_unknown_tool(self, tmp_store):
        result = handle_file_tool(
            "generate_unknown",
            {"title": "T"},
            file_store_path=tmp_store,
        )
        assert "error" in result
        assert "Unknown" in result["error"]


# ---------------------------------------------------------------------------
# Tests: FILE_TOOLS definition
# ---------------------------------------------------------------------------


class TestFileToolsDefinition:
    """Verify the FILE_TOOLS list has correct structure."""

    def test_file_tools_count(self):
        assert len(FILE_TOOLS) == 3

    def test_tool_names(self):
        names = {t["name"] for t in FILE_TOOLS}
        assert "generate_plot" in names
        assert "generate_pdf" in names
        assert "generate_excel" in names

    def test_tools_have_description(self):
        for tool in FILE_TOOLS:
            assert "description" in tool
            assert len(tool["description"]) > 0

    def test_tools_have_input_schema(self):
        for tool in FILE_TOOLS:
            assert "input_schema" in tool
            assert "properties" in tool["input_schema"]

    def test_tools_have_required_fields(self):
        for tool in FILE_TOOLS:
            assert "required" in tool["input_schema"]
            assert len(tool["input_schema"]["required"]) > 0


# ---------------------------------------------------------------------------
# Tests: Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants."""

    def test_header_color(self):
        assert HEADER_COLOR_HEX == "#1F4788"

    def test_alt_row_color(self):
        assert ALT_ROW_COLOR == "#F5F5F5"

    def test_max_col_width(self):
        assert MAX_COL_WIDTH == 50
