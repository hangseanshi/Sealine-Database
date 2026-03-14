"""
Context loader — loads all Markdown (.md) files from a directory recursively
and returns them as concatenated text suitable for the Claude system prompt.

Extracted from claude_desktop.py `load_md_files()` with identical logic.
"""

from __future__ import annotations

import os
import glob
import logging

logger = logging.getLogger(__name__)


def load_md_files(search_root: str) -> tuple[str, list[str]]:
    """
    Recursively load all .md files from *search_root*.

    Returns:
        A tuple of (concatenated_text, list_of_relative_filenames).
        If no files are found, returns ("", []).
    """
    pattern = os.path.join(search_root, "**", "*.md")
    paths = sorted(glob.glob(pattern, recursive=True))

    if not paths:
        logger.info("No .md files found in %s", search_root)
        return "", []

    sections: list[str] = []
    loaded: list[str] = []

    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
            rel = os.path.relpath(path, search_root)
            sections.append(f"## File: {rel}\n\n{content}")
            loaded.append(rel)
        except OSError as exc:
            logger.warning("Failed to read %s: %s", path, exc)

    logger.info("Loaded %d markdown file(s) from %s", len(loaded), search_root)
    return "\n\n---\n\n".join(sections), loaded
