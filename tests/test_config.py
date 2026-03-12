"""
Unit tests for server.config module.

Tests cover:
  - Default values when no env vars are set
  - Environment variable overrides for all config fields
  - db_connection_string property construction
  - Singleton pattern via get_config()
  - Type coercion for integer fields
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from server.config import Config, get_config
import server.config as config_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-level singleton before each test."""
    config_module._config = None
    yield
    config_module._config = None


@pytest.fixture
def clean_env():
    """Provide a context manager that strips all Sealine-related env vars."""
    keys_to_strip = [
        "PORT", "HOST", "WORKERS",
        "MODEL", "ANTHROPIC_API_KEY", "MAX_TOKENS",
        "DB_SERVER", "DB_NAME", "DB_USER", "DB_PASSWORD",
        "SESSION_TTL_HOURS", "FILE_TTL_HOURS",
        "MEMORY_DIR", "SYSTEM_PROMPT",
    ]
    saved = {}
    for k in keys_to_strip:
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    yield
    # Restore original env
    for k in keys_to_strip:
        if k in saved:
            os.environ[k] = saved[k]
        elif k in os.environ:
            del os.environ[k]


# ---------------------------------------------------------------------------
# Tests: Default values
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """Verify that every config field has the expected default value."""

    def test_port_default(self, clean_env):
        cfg = Config()
        assert cfg.PORT == 8080

    def test_host_default(self, clean_env):
        cfg = Config()
        assert cfg.HOST == "0.0.0.0"

    def test_workers_default(self, clean_env):
        cfg = Config()
        assert cfg.WORKERS == 2

    def test_model_default(self, clean_env):
        cfg = Config()
        assert cfg.MODEL == "claude-haiku-4-5"

    def test_anthropic_api_key_default(self, clean_env):
        cfg = Config()
        assert cfg.ANTHROPIC_API_KEY == ""

    def test_max_tokens_default(self, clean_env):
        cfg = Config()
        assert cfg.MAX_TOKENS == 8192

    def test_db_server_default(self, clean_env):
        cfg = Config()
        assert cfg.DB_SERVER == ""

    def test_db_name_default(self, clean_env):
        cfg = Config()
        assert cfg.DB_NAME == ""

    def test_db_user_default(self, clean_env):
        cfg = Config()
        assert cfg.DB_USER == ""

    def test_db_password_default(self, clean_env):
        cfg = Config()
        assert cfg.DB_PASSWORD == ""

    def test_session_ttl_hours_default(self, clean_env):
        cfg = Config()
        assert cfg.SESSION_TTL_HOURS == 2

    def test_file_ttl_hours_default(self, clean_env):
        cfg = Config()
        assert cfg.FILE_TTL_HOURS == 24

    def test_memory_dir_default(self, clean_env):
        cfg = Config()
        assert cfg.MEMORY_DIR == "./memory"

    def test_system_prompt_default(self, clean_env):
        cfg = Config()
        assert "Claude" in cfg.SYSTEM_PROMPT
        assert "Sealine" in cfg.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests: Environment variable overrides
# ---------------------------------------------------------------------------


class TestConfigEnvOverrides:
    """Verify that environment variables override default values."""

    def test_port_override(self, clean_env):
        with patch.dict(os.environ, {"PORT": "9090"}):
            cfg = Config()
            assert cfg.PORT == 9090

    def test_host_override(self, clean_env):
        with patch.dict(os.environ, {"HOST": "127.0.0.1"}):
            cfg = Config()
            assert cfg.HOST == "127.0.0.1"

    def test_workers_override(self, clean_env):
        with patch.dict(os.environ, {"WORKERS": "4"}):
            cfg = Config()
            assert cfg.WORKERS == 4

    def test_model_override(self, clean_env):
        with patch.dict(os.environ, {"MODEL": "claude-sonnet-4-20250514"}):
            cfg = Config()
            assert cfg.MODEL == "claude-sonnet-4-20250514"

    def test_anthropic_api_key_override(self, clean_env):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"}):
            cfg = Config()
            assert cfg.ANTHROPIC_API_KEY == "sk-test-key"

    def test_max_tokens_override(self, clean_env):
        with patch.dict(os.environ, {"MAX_TOKENS": "4096"}):
            cfg = Config()
            assert cfg.MAX_TOKENS == 4096

    def test_db_server_override(self, clean_env):
        with patch.dict(os.environ, {"DB_SERVER": "prod-server"}):
            cfg = Config()
            assert cfg.DB_SERVER == "prod-server"

    def test_db_name_override(self, clean_env):
        with patch.dict(os.environ, {"DB_NAME": "production_db"}):
            cfg = Config()
            assert cfg.DB_NAME == "production_db"

    def test_db_user_override(self, clean_env):
        with patch.dict(os.environ, {"DB_USER": "admin_user"}):
            cfg = Config()
            assert cfg.DB_USER == "admin_user"

    def test_db_password_override(self, clean_env):
        with patch.dict(os.environ, {"DB_PASSWORD": "s3cureP@ss"}):
            cfg = Config()
            assert cfg.DB_PASSWORD == "s3cureP@ss"

    def test_session_ttl_hours_override(self, clean_env):
        with patch.dict(os.environ, {"SESSION_TTL_HOURS": "8"}):
            cfg = Config()
            assert cfg.SESSION_TTL_HOURS == 8

    def test_file_ttl_hours_override(self, clean_env):
        with patch.dict(os.environ, {"FILE_TTL_HOURS": "48"}):
            cfg = Config()
            assert cfg.FILE_TTL_HOURS == 48

    def test_memory_dir_override(self, clean_env):
        with patch.dict(os.environ, {"MEMORY_DIR": "/opt/data/memory"}):
            cfg = Config()
            assert cfg.MEMORY_DIR == "/opt/data/memory"

    def test_system_prompt_override(self, clean_env):
        custom = "You are a custom assistant."
        with patch.dict(os.environ, {"SYSTEM_PROMPT": custom}):
            cfg = Config()
            assert cfg.SYSTEM_PROMPT == custom


# ---------------------------------------------------------------------------
# Tests: db_connection_string property
# ---------------------------------------------------------------------------


class TestDbConnectionString:
    """Verify the ODBC connection string is properly built."""

    def test_connection_string_contains_driver(self, clean_env):
        cfg = Config()
        assert "ODBC Driver 17 for SQL Server" in cfg.db_connection_string

    def test_connection_string_contains_server(self, clean_env):
        with patch.dict(os.environ, {"DB_SERVER": "testserver"}):
            cfg = Config()
            assert "SERVER=testserver;" in cfg.db_connection_string

    def test_connection_string_contains_database(self, clean_env):
        with patch.dict(os.environ, {"DB_NAME": "testdb"}):
            cfg = Config()
            assert "DATABASE=testdb;" in cfg.db_connection_string

    def test_connection_string_contains_uid(self, clean_env):
        with patch.dict(os.environ, {"DB_USER": "testuser"}):
            cfg = Config()
            assert "UID=testuser;" in cfg.db_connection_string

    def test_connection_string_contains_pwd(self, clean_env):
        with patch.dict(os.environ, {"DB_PASSWORD": "testpass"}):
            cfg = Config()
            assert "PWD=testpass;" in cfg.db_connection_string

    def test_connection_string_with_overrides(self, clean_env):
        with patch.dict(os.environ, {
            "DB_SERVER": "myserver",
            "DB_NAME": "mydb",
            "DB_USER": "myuser",
            "DB_PASSWORD": "mypass",
        }):
            cfg = Config()
            cs = cfg.db_connection_string
            assert "SERVER=myserver;" in cs
            assert "DATABASE=mydb;" in cs
            assert "UID=myuser;" in cs
            assert "PWD=mypass;" in cs

    def test_connection_string_format(self, clean_env):
        """Verify the connection string has the expected semicolon-separated format."""
        cfg = Config()
        cs = cfg.db_connection_string
        parts = cs.split(";")
        # Should have at least DRIVER, SERVER, DATABASE, UID, PWD segments
        assert len(parts) >= 5


# ---------------------------------------------------------------------------
# Tests: Singleton pattern
# ---------------------------------------------------------------------------


class TestSingleton:
    """Verify the get_config() singleton pattern."""

    def test_get_config_returns_config_instance(self, clean_env):
        cfg = get_config()
        assert isinstance(cfg, Config)

    def test_get_config_returns_same_instance(self, clean_env):
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_singleton_reset_creates_new_instance(self, clean_env):
        cfg1 = get_config()
        config_module._config = None
        cfg2 = get_config()
        assert cfg1 is not cfg2

    def test_singleton_preserves_values(self, clean_env):
        with patch.dict(os.environ, {"PORT": "3000"}):
            cfg1 = get_config()
        # Even after env changes, singleton should keep original value
        with patch.dict(os.environ, {"PORT": "5000"}):
            cfg2 = get_config()
        assert cfg2.PORT == 3000  # same instance, created with PORT=3000


# ---------------------------------------------------------------------------
# Tests: Type coercion edge cases
# ---------------------------------------------------------------------------


class TestTypeCoercion:
    """Verify int fields handle string-to-int coercion."""

    def test_invalid_port_raises(self, clean_env):
        with patch.dict(os.environ, {"PORT": "not_a_number"}):
            with pytest.raises(ValueError):
                Config()

    def test_invalid_workers_raises(self, clean_env):
        with patch.dict(os.environ, {"WORKERS": "abc"}):
            with pytest.raises(ValueError):
                Config()

    def test_invalid_max_tokens_raises(self, clean_env):
        with patch.dict(os.environ, {"MAX_TOKENS": "xyz"}):
            with pytest.raises(ValueError):
                Config()

    def test_zero_port_is_valid(self, clean_env):
        with patch.dict(os.environ, {"PORT": "0"}):
            cfg = Config()
            assert cfg.PORT == 0

    def test_negative_ttl_is_valid(self, clean_env):
        """Negative values aren't validated at the Config level."""
        with patch.dict(os.environ, {"SESSION_TTL_HOURS": "-1"}):
            cfg = Config()
            assert cfg.SESSION_TTL_HOURS == -1
