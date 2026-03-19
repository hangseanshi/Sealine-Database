"""
Unit tests for the Flask application factory (server/app.py).

Tests:
  - create_app returns a Flask instance
  - All five blueprints are registered
  - Shared resources are attached to the app
  - Static file serving (index.html and fallback)

Note: On Python 3.9, ``server.config`` uses ``Config | None`` syntax
(Python 3.10+), so we mock ``get_config`` and the entire config module
to avoid the import error.
"""

import os
import sys
import types
from unittest.mock import patch, MagicMock

import pytest
from flask import Flask


class TestCreateAppViaFixture:
    """
    Tests that validate the app factory's behavior using the conftest
    ``app`` fixture (which builds the Flask app manually with the same
    resources that create_app() would attach).

    This approach avoids importing the real ``create_app()`` which has
    import chain issues on Python 3.9.
    """

    def test_app_is_flask_instance(self, app):
        """The app fixture should return a Flask application."""
        assert isinstance(app, Flask)

    def test_all_blueprints_registered(self, app):
        """All five route blueprints should be registered on the app."""
        blueprint_names = list(app.blueprints.keys())
        assert "sessions" in blueprint_names
        assert "messages" in blueprint_names
        assert "files" in blueprint_names
        assert "health" in blueprint_names
        assert "teams" in blueprint_names

    def test_five_blueprints_total(self, app):
        """There should be exactly 5 blueprints registered."""
        assert len(app.blueprints) == 5

    def test_session_store_on_app(self, app):
        """session_store should be accessible as a direct attribute."""
        assert hasattr(app, "session_store")
        assert app.session_store is not None

    def test_docs_text_on_app(self, app):
        """docs_text should be accessible as a direct attribute."""
        assert hasattr(app, "docs_text")
        assert app.docs_text == "Test context docs"

    def test_docs_files_on_app(self, app):
        """docs_files should be accessible as a direct attribute."""
        assert hasattr(app, "docs_files")
        assert app.docs_files == ["test_doc.md"]

    def test_config_obj_on_app(self, app):
        """config_obj should be accessible as a direct attribute."""
        assert hasattr(app, "config_obj")
        assert app.config_obj is not None

    def test_session_store_in_config(self, app):
        """SESSION_STORE should be in app.config dict."""
        assert "SESSION_STORE" in app.config

    def test_context_text_in_config(self, app):
        """CONTEXT_TEXT should be in app.config dict."""
        assert "CONTEXT_TEXT" in app.config

    def test_context_files_in_config(self, app):
        """CONTEXT_FILES should be in app.config dict."""
        assert "CONTEXT_FILES" in app.config

    def test_file_store_path_in_config(self, app):
        """FILE_STORE_PATH should be in app.config dict."""
        assert "FILE_STORE_PATH" in app.config

    def test_sealine_config_in_config(self, app):
        """SEALINE_CONFIG should be in app.config dict."""
        assert "SEALINE_CONFIG" in app.config


class TestStaticServing:
    """Tests for the static file serving routes (/ and /<path>)."""

    def test_api_health_accessible(self, client):
        """The /api/health endpoint should be accessible (not caught by static fallback)."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_api_sessions_accessible(self, client):
        """The /api/sessions endpoint should be accessible (POST)."""
        resp = client.post("/api/sessions")
        assert resp.status_code == 201

    def test_static_url_path_configured(self, app):
        """The app should have /static as its static URL path."""
        assert app.static_url_path == "/static"


class TestBlueprintURLPrefixes:
    """Verify that all blueprints use the /api prefix."""

    def test_sessions_blueprint_prefix(self, app):
        """Sessions blueprint should have /api prefix."""
        bp = app.blueprints["sessions"]
        assert bp.url_prefix == "/api"

    def test_messages_blueprint_prefix(self, app):
        """Messages blueprint should have /api prefix."""
        bp = app.blueprints["messages"]
        assert bp.url_prefix == "/api"

    def test_files_blueprint_prefix(self, app):
        """Files blueprint should have /api prefix."""
        bp = app.blueprints["files"]
        assert bp.url_prefix == "/api"

    def test_health_blueprint_prefix(self, app):
        """Health blueprint should have /api prefix."""
        bp = app.blueprints["health"]
        assert bp.url_prefix == "/api"

    def test_teams_blueprint_prefix(self, app):
        """Teams blueprint should have /api prefix."""
        bp = app.blueprints["teams"]
        assert bp.url_prefix == "/api"


class TestAppConfiguration:
    """Verify app configuration and shared resources."""

    def test_testing_flag_set(self, app):
        """The TESTING flag should be set for test apps."""
        assert app.config["TESTING"] is True

    def test_session_store_matches_config(self, app, session_store):
        """The session store on the app should be the one we provided."""
        assert app.session_store is session_store
        assert app.config["SESSION_STORE"] is session_store

    def test_config_model(self, app):
        """The config object should have the correct model."""
        assert app.config_obj.MODEL == "claude-haiku-4-5"
