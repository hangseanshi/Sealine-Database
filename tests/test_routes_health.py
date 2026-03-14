"""
Unit tests for the Health route (server/routes/health.py).

Tests:
  - GET /api/health returns 200
  - Response contains correct JSON structure with all required fields
  - DB connection check results propagated correctly
  - Active session count is accurate
"""

import json
import time
from unittest.mock import patch, MagicMock

import pytest


class TestHealthEndpoint:
    """GET /api/health"""

    def test_health_returns_200(self, client):
        """The health endpoint should return 200."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_health_returns_json(self, client):
        """The response should be JSON."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        assert resp.content_type.startswith("application/json")

    def test_health_contains_status(self, client):
        """The response should contain a 'status' field set to 'healthy'."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert data["status"] == "healthy"

    def test_health_contains_version(self, client):
        """The response should contain a 'version' field."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert "version" in data
        assert data["version"] == "1.0.0"

    def test_health_contains_model(self, client):
        """The response should contain a 'model' field matching config."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert "model" in data
        assert data["model"] == "claude-haiku-4-5"

    def test_health_contains_db_connected(self, client):
        """The response should contain a 'db_connected' boolean field."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert "db_connected" in data
        assert isinstance(data["db_connected"], bool)

    def test_health_contains_uptime(self, client):
        """The response should contain an 'uptime_seconds' numeric field."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0

    def test_health_contains_active_sessions(self, client):
        """The response should contain an 'active_sessions' integer field."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert "active_sessions" in data
        assert isinstance(data["active_sessions"], int)


class TestHealthDBConnection:
    """DB connectivity reporting in the health endpoint."""

    def test_db_connected_true(self, client):
        """When DB connection succeeds, db_connected should be True."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert data["db_connected"] is True

    def test_db_connected_false(self, client):
        """When DB connection fails, db_connected should be False."""
        with patch("server.routes.health._check_db_connection", return_value=False):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert data["db_connected"] is False

    def test_health_still_200_when_db_down(self, client):
        """Even when DB is down, the health endpoint should return 200 (service is up)."""
        with patch("server.routes.health._check_db_connection", return_value=False):
            resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"


class TestHealthActiveSessions:
    """Active session count in the health endpoint."""

    def test_zero_sessions_initially(self, client):
        """With no sessions created, active_sessions should be 0."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert data["active_sessions"] == 0

    def test_active_sessions_count_after_creation(self, client, session_store):
        """After creating sessions, active_sessions should reflect the count."""
        session_store.create()
        session_store.create()

        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert data["active_sessions"] == 2

    def test_active_sessions_decreases_after_delete(self, client, session_store):
        """After deleting a session, active_sessions should decrease."""
        s = session_store.create()
        session_store.create()
        session_store.delete(s.session_id)

        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()
        assert data["active_sessions"] == 1


class TestHealthJSONStructure:
    """Verify the complete JSON structure of the health response."""

    def test_complete_structure(self, client):
        """The response should contain exactly the expected top-level keys."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()

        expected_keys = {"status", "version", "model", "db_connected", "uptime_seconds", "active_sessions"}
        assert set(data.keys()) == expected_keys

    def test_no_extra_fields(self, client):
        """There should be no unexpected fields in the response."""
        with patch("server.routes.health._check_db_connection", return_value=True):
            resp = client.get("/api/health")
        data = resp.get_json()

        expected_keys = {"status", "version", "model", "db_connected", "uptime_seconds", "active_sessions"}
        extra = set(data.keys()) - expected_keys
        assert len(extra) == 0, f"Unexpected fields: {extra}"
