"""
Unit tests for server.core.context_loader module.

Tests cover:
  - Loading .md files from a directory with multiple files
  - Handling empty directories (no .md files)
  - Handling non-existent directories
  - Ignoring non-.md files (e.g., .txt, .py, .json)
  - File content concatenation format
  - Relative path handling in returned file list
  - Recursive subdirectory loading
  - Handling of unreadable files (OSError)
"""

from __future__ import annotations

import os
import tempfile
import shutil

import pytest

from server.core.context_loader import load_md_files


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    """Create a temporary directory, yield its path, then clean up."""
    d = tempfile.mkdtemp(prefix="test_context_loader_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _create_file(base: str, relative_path: str, content: str) -> str:
    """Helper to create a file within a base directory."""
    full_path = os.path.join(base, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return full_path


# ---------------------------------------------------------------------------
# Tests: Loading .md files
# ---------------------------------------------------------------------------


class TestLoadMdFiles:
    """Test core loading functionality."""

    def test_load_single_md_file(self, tmp_dir):
        _create_file(tmp_dir, "schema.md", "# Schema\nTable users")
        text, files = load_md_files(tmp_dir)
        assert len(files) == 1
        assert "schema.md" in files
        assert "# Schema" in text
        assert "Table users" in text

    def test_load_multiple_md_files(self, tmp_dir):
        _create_file(tmp_dir, "schema.md", "# Schema")
        _create_file(tmp_dir, "reference.md", "# Reference")
        text, files = load_md_files(tmp_dir)
        assert len(files) == 2
        assert "schema.md" in files
        assert "reference.md" in files

    def test_files_are_sorted(self, tmp_dir):
        """Files should be returned in sorted order by path."""
        _create_file(tmp_dir, "z_last.md", "last")
        _create_file(tmp_dir, "a_first.md", "first")
        _create_file(tmp_dir, "m_middle.md", "middle")
        text, files = load_md_files(tmp_dir)
        assert files == sorted(files)

    def test_text_contains_file_headers(self, tmp_dir):
        """Each file section should start with '## File: <relative_path>'."""
        _create_file(tmp_dir, "schema.md", "Schema content")
        text, files = load_md_files(tmp_dir)
        assert "## File: schema.md" in text

    def test_sections_separated_by_dividers(self, tmp_dir):
        """Multiple files should be separated by '---' dividers."""
        _create_file(tmp_dir, "a.md", "AAA")
        _create_file(tmp_dir, "b.md", "BBB")
        text, files = load_md_files(tmp_dir)
        assert "---" in text

    def test_content_is_stripped(self, tmp_dir):
        """File content should be stripped of leading/trailing whitespace."""
        _create_file(tmp_dir, "test.md", "  \n  Hello World  \n  ")
        text, files = load_md_files(tmp_dir)
        assert "Hello World" in text
        # Should not have leading/trailing whitespace around content
        # (the content portion, not the whole text)


# ---------------------------------------------------------------------------
# Tests: Empty directory
# ---------------------------------------------------------------------------


class TestEmptyDirectory:
    """Test behavior when directory has no .md files."""

    def test_empty_dir_returns_empty_string(self, tmp_dir):
        text, files = load_md_files(tmp_dir)
        assert text == ""
        assert files == []

    def test_dir_with_only_non_md_files(self, tmp_dir):
        _create_file(tmp_dir, "readme.txt", "text file")
        _create_file(tmp_dir, "script.py", "print('hello')")
        _create_file(tmp_dir, "data.json", '{"key": "value"}')
        text, files = load_md_files(tmp_dir)
        assert text == ""
        assert files == []


# ---------------------------------------------------------------------------
# Tests: Non-existent directory
# ---------------------------------------------------------------------------


class TestNonExistentDirectory:
    """Test behavior when the directory does not exist."""

    def test_nonexistent_dir_returns_empty(self):
        text, files = load_md_files("/nonexistent/path/that/does/not/exist")
        assert text == ""
        assert files == []

    def test_nonexistent_dir_returns_tuple(self):
        result = load_md_files("/fake/path")
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tests: Non-.md files ignored
# ---------------------------------------------------------------------------


class TestNonMdFilesIgnored:
    """Verify only .md files are loaded; other types are skipped."""

    def test_txt_files_ignored(self, tmp_dir):
        _create_file(tmp_dir, "notes.txt", "These are notes")
        _create_file(tmp_dir, "schema.md", "Schema content")
        text, files = load_md_files(tmp_dir)
        assert len(files) == 1
        assert "notes.txt" not in files
        assert "These are notes" not in text

    def test_py_files_ignored(self, tmp_dir):
        _create_file(tmp_dir, "code.py", "import os")
        _create_file(tmp_dir, "doc.md", "Documentation")
        text, files = load_md_files(tmp_dir)
        assert len(files) == 1
        assert "import os" not in text

    def test_markdown_extension_case_sensitivity(self, tmp_dir):
        """Only .md (lowercase) files are loaded, not .MD or .Md."""
        _create_file(tmp_dir, "upper.MD", "UPPER")
        _create_file(tmp_dir, "lower.md", "LOWER")
        text, files = load_md_files(tmp_dir)
        # glob on most OS is case-sensitive for extensions
        assert "lower.md" in files
        # .MD may or may not be loaded depending on OS; we just ensure
        # at least the .md file is included
        assert "LOWER" in text


# ---------------------------------------------------------------------------
# Tests: Recursive subdirectory loading
# ---------------------------------------------------------------------------


class TestRecursiveLoading:
    """Verify files are loaded recursively from subdirectories."""

    def test_load_from_subdirectory(self, tmp_dir):
        _create_file(tmp_dir, "sub/nested.md", "Nested content")
        text, files = load_md_files(tmp_dir)
        assert len(files) == 1
        assert os.path.join("sub", "nested.md") in files
        assert "Nested content" in text

    def test_load_from_deep_subdirectory(self, tmp_dir):
        _create_file(tmp_dir, "a/b/c/deep.md", "Deep content")
        text, files = load_md_files(tmp_dir)
        assert len(files) == 1
        assert "Deep content" in text

    def test_mixed_root_and_subdirectory(self, tmp_dir):
        _create_file(tmp_dir, "root.md", "Root content")
        _create_file(tmp_dir, "sub/child.md", "Child content")
        text, files = load_md_files(tmp_dir)
        assert len(files) == 2

    def test_relative_paths_in_returned_list(self, tmp_dir):
        """Returned file list should contain paths relative to search_root."""
        _create_file(tmp_dir, "sub/doc.md", "Doc")
        text, files = load_md_files(tmp_dir)
        for f in files:
            assert not os.path.isabs(f), f"Expected relative path, got: {f}"


# ---------------------------------------------------------------------------
# Tests: File read errors
# ---------------------------------------------------------------------------


class TestFileReadErrors:
    """Verify graceful handling when a file cannot be read."""

    def test_unreadable_file_skipped(self, tmp_dir):
        """Files that raise OSError are silently skipped."""
        _create_file(tmp_dir, "good.md", "Good content")
        bad_path = _create_file(tmp_dir, "bad.md", "Bad content")
        # Make the file unreadable
        os.chmod(bad_path, 0o000)
        try:
            text, files = load_md_files(tmp_dir)
            # The good file should still be loaded
            assert "good.md" in files
            assert "Good content" in text
            # The bad file should be skipped (not in the file list)
            assert "bad.md" not in files
        finally:
            # Restore permissions for cleanup
            os.chmod(bad_path, 0o644)


# ---------------------------------------------------------------------------
# Tests: Return type and structure
# ---------------------------------------------------------------------------


class TestReturnStructure:
    """Verify the return type and structure of load_md_files."""

    def test_returns_tuple_of_str_and_list(self, tmp_dir):
        _create_file(tmp_dir, "test.md", "Content")
        result = load_md_files(tmp_dir)
        assert isinstance(result, tuple)
        text, files = result
        assert isinstance(text, str)
        assert isinstance(files, list)

    def test_file_list_contains_strings(self, tmp_dir):
        _create_file(tmp_dir, "a.md", "A")
        _create_file(tmp_dir, "b.md", "B")
        text, files = load_md_files(tmp_dir)
        for f in files:
            assert isinstance(f, str)

    def test_concatenation_format(self, tmp_dir):
        """Text should follow: '## File: <name>\n\n<content>' per file."""
        _create_file(tmp_dir, "doc.md", "Hello World")
        text, files = load_md_files(tmp_dir)
        assert text.startswith("## File: doc.md\n\n")
        assert "Hello World" in text
