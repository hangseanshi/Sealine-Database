"""
Unit tests for the Messages route (server/routes/messages.py).

Tests:
  - POST /api/sessions/<id>/messages with valid message returns SSE stream (200)
  - POST without message returns 400
  - POST to non-existent session returns 404
  - SSE events include message_start and message_end
  - Error handling in stream (agent raises exception)
"""

import json
import sys
from unittest.mock import MagicMock

import pytest


def parse_sse_events(response_data):
    """
    Parse raw SSE text into a list of (event_name, data_dict) tuples.
    SSE format: "event: <name>\ndata: <json>\n\n"
    """
    events = []
    text = response_data.decode("utf-8") if isinstance(response_data, bytes) else response_data
    blocks = text.strip().split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        event_name = None
        data_str = None
        for line in lines:
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_str = line[len("data: "):]
        if event_name and data_str:
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                data = {"raw": data_str}
            events.append((event_name, data))
    return events


def _set_mock_agent(agent_instance):
    """Helper to set the SealineAgent in sys.modules to return a given instance."""
    mock_class = MagicMock(return_value=agent_instance)
    sys.modules["server.core.agent"].SealineAgent = mock_class
    return mock_class


def _make_agent_instance(**overrides):
    """Create a MagicMock agent instance with default attributes."""
    agent = MagicMock()
    agent.send_message.return_value = iter(overrides.get("events", []))
    agent.messages = overrides.get("messages", [])
    agent.total_input_tokens = overrides.get("total_input_tokens", 0)
    agent.total_output_tokens = overrides.get("total_output_tokens", 0)
    agent.cache_hits = overrides.get("cache_hits", 0)
    agent.sql_calls = overrides.get("sql_calls", 0)
    agent.generated_files = overrides.get("generated_files", [])
    return agent


class TestSendMessageValidation:
    """Request validation for POST /api/sessions/<session_id>/messages"""

    def test_missing_json_body_returns_400(self, client, created_session):
        """POST without a JSON body should return 400."""
        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    def test_empty_message_returns_400(self, client, created_session):
        """POST with empty message field should return 400."""
        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": ""},
        )
        assert resp.status_code == 400

    def test_missing_message_field_returns_400(self, client, created_session):
        """POST without a message field should return 400."""
        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"other": "value"},
        )
        assert resp.status_code == 400

    def test_whitespace_only_message_returns_400(self, client, created_session):
        """POST with whitespace-only message should return 400."""
        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "   "},
        )
        assert resp.status_code == 400

    def test_nonexistent_session_returns_404(self, client):
        """POST to a non-existent session should return 404."""
        resp = client.post(
            "/api/sessions/nonexistent-id/messages",
            json={"message": "hello"},
        )
        assert resp.status_code == 404

    def test_nonexistent_session_error_code(self, client):
        """404 response should have SESSION_NOT_FOUND code."""
        resp = client.post(
            "/api/sessions/nonexistent-id/messages",
            json={"message": "hello"},
        )
        data = resp.get_json()
        assert data["code"] == "SESSION_NOT_FOUND"

    def test_non_string_message_returns_400(self, client, created_session):
        """POST with non-string message should return 400."""
        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": 12345},
        )
        assert resp.status_code == 400


class TestSendMessageStreaming:
    """SSE streaming behavior for POST /api/sessions/<session_id>/messages"""

    def test_valid_message_returns_sse_content_type(self, client, created_session, mock_agent):
        """Response Content-Type should be text/event-stream."""
        agent_instance, _ = mock_agent
        agent_instance.send_message.return_value = iter([
            {"event": "text_delta", "data": {"delta": "Hello"}},
        ])

        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "hello"},
        )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type

    def test_sse_stream_includes_message_start(self, client, created_session, mock_agent):
        """The SSE stream should begin with a message_start event."""
        agent_instance, _ = mock_agent
        agent_instance.send_message.return_value = iter([
            {"event": "text_delta", "data": {"delta": "Hi"}},
        ])

        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "hello"},
        )

        events = parse_sse_events(resp.data)
        event_names = [e[0] for e in events]
        assert event_names[0] == "message_start"

    def test_sse_stream_includes_message_end(self, client, created_session, mock_agent):
        """The SSE stream should end with a message_end event."""
        agent_instance, _ = mock_agent
        agent_instance.send_message.return_value = iter([
            {"event": "text_delta", "data": {"delta": "Hi"}},
        ])
        agent_instance.total_input_tokens = 100
        agent_instance.total_output_tokens = 50
        agent_instance.cache_hits = 1
        agent_instance.sql_calls = 2

        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "hello"},
        )

        events = parse_sse_events(resp.data)
        event_names = [e[0] for e in events]
        assert event_names[-1] == "message_end"

    def test_message_end_contains_usage(self, client, created_session, mock_agent):
        """The message_end event should contain usage stats."""
        agent_instance, _ = mock_agent
        agent_instance.send_message.return_value = iter([])
        agent_instance.total_input_tokens = 100
        agent_instance.total_output_tokens = 50
        agent_instance.cache_hits = 3
        agent_instance.sql_calls = 1

        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "hello"},
        )

        events = parse_sse_events(resp.data)
        end_events = [e for e in events if e[0] == "message_end"]
        assert len(end_events) == 1
        end_data = end_events[0][1]
        assert "usage" in end_data
        assert end_data["usage"]["input_tokens"] == 100
        assert end_data["usage"]["output_tokens"] == 50

    def test_message_start_contains_ids(self, client, created_session, mock_agent):
        """The message_start event should contain message_id and session_id."""
        agent_instance, _ = mock_agent
        agent_instance.send_message.return_value = iter([])

        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "hello"},
        )

        events = parse_sse_events(resp.data)
        start_events = [e for e in events if e[0] == "message_start"]
        assert len(start_events) == 1
        start_data = start_events[0][1]
        assert "message_id" in start_data
        assert start_data["session_id"] == created_session.session_id

    def test_text_delta_events_forwarded(self, client, created_session, mock_agent):
        """text_delta events from the agent should appear in the SSE stream."""
        agent_instance, _ = mock_agent
        agent_instance.send_message.return_value = iter([
            {"event": "text_delta", "data": {"delta": "Hello "}},
            {"event": "text_delta", "data": {"delta": "world"}},
        ])

        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "hello"},
        )

        events = parse_sse_events(resp.data)
        text_deltas = [e for e in events if e[0] == "text_delta"]
        assert len(text_deltas) == 2
        assert text_deltas[0][1]["delta"] == "Hello "
        assert text_deltas[1][1]["delta"] == "world"

    def test_agent_message_start_end_filtered_out(self, client, created_session, mock_agent):
        """message_start and message_end from the agent should be filtered out."""
        agent_instance, _ = mock_agent
        agent_instance.send_message.return_value = iter([
            {"event": "message_start", "data": {}},
            {"event": "text_delta", "data": {"delta": "Hi"}},
            {"event": "message_end", "data": {}},
        ])

        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "hello"},
        )

        events = parse_sse_events(resp.data)
        starts = [e for e in events if e[0] == "message_start"]
        ends = [e for e in events if e[0] == "message_end"]
        assert len(starts) == 1  # Only our wrapper's message_start
        assert len(ends) == 1    # Only our wrapper's message_end

    def test_response_headers(self, client, created_session, mock_agent):
        """The streaming response should include Cache-Control and X-Accel-Buffering headers."""
        agent_instance, _ = mock_agent
        agent_instance.send_message.return_value = iter([])

        resp = client.post(
            f"/api/sessions/{created_session.session_id}/messages",
            json={"message": "hello"},
        )

        assert resp.headers.get("Cache-Control") == "no-cache"
        assert resp.headers.get("X-Accel-Buffering") == "no"


class TestSendMessageErrorHandling:
    """Error handling in the SSE stream."""

    def test_agent_exception_emits_error_event(self, client, created_session):
        """If the agent raises an exception, an error SSE event should be emitted."""
        mock_class = MagicMock(side_effect=RuntimeError("Something broke"))
        old = sys.modules["server.core.agent"].SealineAgent
        sys.modules["server.core.agent"].SealineAgent = mock_class

        try:
            resp = client.post(
                f"/api/sessions/{created_session.session_id}/messages",
                json={"message": "hello"},
            )

            assert resp.status_code == 200  # SSE stream still returns 200
            events = parse_sse_events(resp.data)
            error_events = [e for e in events if e[0] == "error"]
            assert len(error_events) == 1
            assert "Something broke" in error_events[0][1]["error"]
            assert error_events[0][1]["code"] == "AGENT_ERROR"
        finally:
            sys.modules["server.core.agent"].SealineAgent = old

    def test_error_still_emits_message_end(self, client, created_session):
        """Even after an error, message_end should still be emitted."""
        mock_class = MagicMock(side_effect=RuntimeError("fail"))
        old = sys.modules["server.core.agent"].SealineAgent
        sys.modules["server.core.agent"].SealineAgent = mock_class

        try:
            resp = client.post(
                f"/api/sessions/{created_session.session_id}/messages",
                json={"message": "hello"},
            )

            events = parse_sse_events(resp.data)
            event_names = [e[0] for e in events]
            assert "message_end" in event_names
        finally:
            sys.modules["server.core.agent"].SealineAgent = old

    def test_rate_limit_error_code(self, client, created_session):
        """A RateLimitError should produce a RATE_LIMITED error code."""

        class RateLimitError(Exception):
            pass

        mock_class = MagicMock(side_effect=RateLimitError("rate limited"))
        old = sys.modules["server.core.agent"].SealineAgent
        sys.modules["server.core.agent"].SealineAgent = mock_class

        try:
            resp = client.post(
                f"/api/sessions/{created_session.session_id}/messages",
                json={"message": "hello"},
            )

            events = parse_sse_events(resp.data)
            error_events = [e for e in events if e[0] == "error"]
            assert len(error_events) == 1
            assert error_events[0][1]["code"] == "RATE_LIMITED"
        finally:
            sys.modules["server.core.agent"].SealineAgent = old

    def test_auth_error_code(self, client, created_session):
        """An AuthenticationError should produce a CLAUDE_API_ERROR code."""

        class AuthenticationError(Exception):
            pass

        mock_class = MagicMock(side_effect=AuthenticationError("auth failed"))
        old = sys.modules["server.core.agent"].SealineAgent
        sys.modules["server.core.agent"].SealineAgent = mock_class

        try:
            resp = client.post(
                f"/api/sessions/{created_session.session_id}/messages",
                json={"message": "hello"},
            )

            events = parse_sse_events(resp.data)
            error_events = [e for e in events if e[0] == "error"]
            assert len(error_events) == 1
            assert error_events[0][1]["code"] == "CLAUDE_API_ERROR"
        finally:
            sys.modules["server.core.agent"].SealineAgent = old

    def test_error_event_has_recoverable_field(self, client, created_session):
        """Error events should include a recoverable field."""
        mock_class = MagicMock(side_effect=RuntimeError("fail"))
        old = sys.modules["server.core.agent"].SealineAgent
        sys.modules["server.core.agent"].SealineAgent = mock_class

        try:
            resp = client.post(
                f"/api/sessions/{created_session.session_id}/messages",
                json={"message": "hello"},
            )

            events = parse_sse_events(resp.data)
            error_events = [e for e in events if e[0] == "error"]
            assert "recoverable" in error_events[0][1]
            assert error_events[0][1]["recoverable"] is False
        finally:
            sys.modules["server.core.agent"].SealineAgent = old
