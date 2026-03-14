"""
Configuration module for Sealine Data Chat API.

Loads all settings from environment variables with sensible defaults.
See PRD Section 12.3 for the full list of environment variables.
"""

from __future__ import annotations

import os


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self):
        # --- Server ---
        self.PORT: int = int(os.environ.get("PORT", "8080"))
        self.HOST: str = os.environ.get("HOST", "0.0.0.0")
        self.WORKERS: int = int(os.environ.get("WORKERS", "2"))

        # --- Claude API ---
        self.MODEL: str = os.environ.get("MODEL", "claude-haiku-4-5")
        self.ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
        self.MAX_TOKENS: int = int(os.environ.get("MAX_TOKENS", "8192"))

        # --- Database ---
        self.DB_SERVER: str = os.environ.get("DB_SERVER", "")
        self.DB_NAME: str = os.environ.get("DB_NAME", "")
        self.DB_USER: str = os.environ.get("DB_USER", "")
        self.DB_PASSWORD: str = os.environ.get("DB_PASSWORD", "")

        # --- Sessions & Files ---
        self.SESSION_TTL_HOURS: int = int(os.environ.get("SESSION_TTL_HOURS", "2"))
        self.FILE_TTL_HOURS: int = int(os.environ.get("FILE_TTL_HOURS", "24"))

        # --- Memory / Context ---
        self.MEMORY_DIR: str = os.environ.get("MEMORY_DIR", "./memory")

        # --- System prompt ---
        self.SYSTEM_PROMPT: str = os.environ.get(
            "SYSTEM_PROMPT",
            "You are Claude, a helpful AI assistant and data analyst "
            "for the Sealine shipping database. You have been given "
            "the database schema and reference documents as context.",
        )

    @property
    def db_connection_string(self) -> str:
        """Build the ODBC connection string from config values."""
        return (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={self.DB_SERVER};"
            f"DATABASE={self.DB_NAME};"
            f"UID={self.DB_USER};"
            f"PWD={self.DB_PASSWORD};"
        )


# Module-level singleton
_config: Config | None = None


def get_config() -> Config:
    """Return the singleton Config instance, creating it on first call."""
    global _config
    if _config is None:
        _config = Config()
    return _config
