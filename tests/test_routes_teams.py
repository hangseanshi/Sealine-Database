"""
Unit tests for the Teams webhook route (server/routes/teams.py).

Tests:
  - POST /api/teams/messages with valid message activity returns 200
  - POST with non-message activity returns 200 with empty body
  - POST without text returns 400
  - POST without conversation.id returns 400
  - Mention stripping (<at>BotName</at>) works correctly
  - Error handling (agent errors)
  - Session reuse for same conversation ID
"""

import json
import sys
from unittest.mock import MagicMock

import pytest


def _make_activity(text="hello", activity_type="message", conversation_id="conv-123"):
    """Helper to build a valid Bot Framework Activity dict."""
    return {
        "type": activity_type,
        "text": text,
        "conversation": {"id": conversation_id},
        "from": {"id": "user-1", "name": "Test User"},
        "recipient": {"id": "bot-1", "name": "Sealine Bot"},
        "id": "activity-001",
    }


def _setup_mock_agent():
    """Create and install a mock agent in sys.modules. Returns (instance, old_class)."""
    agent_instance = MagicMock()
    agent_instance.send_message_sync.return_value = "Mock reply"
    agent_instance.messages = []
    agent_instance.total_input_tokens = 0
    agent_instance.total_output_tokens = 0
    agent_instance.cache_hits = 0
    agent_instance.sql_calls = 0
    agent_instance.generated_files = []

    mock_class = MagicMock(return_value=agent_instance)
    old = sys.modules["server.core.agent"].SealineAgent
    sys.modules["server.core.agent"].SealineAgent = mock_class
    return agent_instance, mock_class, old


def _restore_mock_agent(old):
    """Restore the original SealineAgent in sys.modules."""
    sys.modules["server.core.agent"].SealineAgent = old


class TestTeamsWebhookValidation:
    """Request validation for POST /api/teams/messages"""

    def test_valid_message_returns_200(self, client):
        """A valid message activity should return 200."""
        agent_instance, mock_class, old = _setup_mock_agent()
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="hello"),
            )
            assert resp.status_code == 200
        finally:
            _restore_mock_agent(old)

    def test_non_message_activity_returns_200_empty(self, client):
        """Non-message activities (e.g. conversationUpdate) should return 200 with empty JSON."""
        resp = client.post(
            "/api/teams/messages",
            json=_make_activity(activity_type="conversationUpdate"),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {}

    def test_invalid_json_returns_400(self, client):
        """POST with invalid JSON should return 400."""
        resp = client.post(
            "/api/teams/messages",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    def test_empty_text_returns_400(self, client):
        """A message activity with empty text should return 400."""
        resp = client.post(
            "/api/teams/messages",
            json=_make_activity(text=""),
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["code"] == "INVALID_REQUEST"

    def test_missing_conversation_id_returns_400(self, client):
        """A message activity without conversation.id should return 400."""
        activity = _make_activity(text="hello")
        activity["conversation"] = {}  # no id

        resp = client.post(
            "/api/teams/messages",
            json=activity,
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["code"] == "INVALID_REQUEST"

    def test_missing_conversation_object_returns_400(self, client):
        """A message activity without conversation object should return 400."""
        activity = _make_activity(text="hello")
        del activity["conversation"]

        resp = client.post(
            "/api/teams/messages",
            json=activity,
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["code"] == "INVALID_REQUEST"


class TestTeamsReplyFormat:
    """Verify the Bot Framework Activity reply format."""

    def test_reply_is_message_type(self, client):
        """The reply should have type 'message'."""
        agent_instance, _, old = _setup_mock_agent()
        agent_instance.send_message_sync.return_value = "Bot reply"
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(),
            )
            data = resp.get_json()
            assert data["type"] == "message"
        finally:
            _restore_mock_agent(old)

    def test_reply_contains_text(self, client):
        """The reply should contain the agent's response text."""
        agent_instance, _, old = _setup_mock_agent()
        agent_instance.send_message_sync.return_value = "Here are the results"
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(),
            )
            data = resp.get_json()
            assert data["text"] == "Here are the results"
        finally:
            _restore_mock_agent(old)

    def test_reply_has_markdown_format(self, client):
        """The reply should specify textFormat as markdown."""
        agent_instance, _, old = _setup_mock_agent()
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(),
            )
            data = resp.get_json()
            assert data["textFormat"] == "markdown"
        finally:
            _restore_mock_agent(old)

    def test_reply_swaps_from_and_recipient(self, client):
        """The reply's 'from' should be the original recipient, and vice versa."""
        agent_instance, _, old = _setup_mock_agent()
        activity = _make_activity()
        original_from = activity["from"]
        original_recipient = activity["recipient"]

        try:
            resp = client.post(
                "/api/teams/messages",
                json=activity,
            )
            data = resp.get_json()
            assert data["from"] == original_recipient
            assert data["recipient"] == original_from
        finally:
            _restore_mock_agent(old)

    def test_reply_includes_reply_to_id(self, client):
        """The reply should reference the original activity ID via replyToId."""
        agent_instance, _, old = _setup_mock_agent()
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(),
            )
            data = resp.get_json()
            assert data["replyToId"] == "activity-001"
        finally:
            _restore_mock_agent(old)


class TestMentionStripping:
    """Test the _strip_mention function behavior."""

    def test_strips_at_mention(self, client):
        """<at>BotName</at> should be stripped from the message."""
        agent_instance, _, old = _setup_mock_agent()
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="<at>Sealine Bot</at> how many containers?"),
            )
            # The agent should have received the message WITHOUT the mention tag
            agent_instance.send_message_sync.assert_called_once_with("how many containers?")
        finally:
            _restore_mock_agent(old)

    def test_strips_multiple_mentions(self, client):
        """Multiple <at> tags should all be stripped."""
        agent_instance, _, old = _setup_mock_agent()
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="<at>Bot1</at> <at>Bot2</at> test message"),
            )
            agent_instance.send_message_sync.assert_called_once_with("test message")
        finally:
            _restore_mock_agent(old)

    def test_plain_text_passes_through(self, client):
        """Text without <at> tags should pass through unchanged."""
        agent_instance, _, old = _setup_mock_agent()
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="plain message"),
            )
            agent_instance.send_message_sync.assert_called_once_with("plain message")
        finally:
            _restore_mock_agent(old)

    def test_mention_only_text_returns_400(self, client):
        """If text is only a mention tag with no actual content, return 400."""
        resp = client.post(
            "/api/teams/messages",
            json=_make_activity(text="<at>Sealine Bot</at>"),
        )
        assert resp.status_code == 400


class TestTeamsSessionManagement:
    """Test session reuse for Teams conversations."""

    def test_same_conversation_reuses_session(self, client, session_store):
        """Two messages in the same conversation should use the same session."""
        agent_instance, _, old = _setup_mock_agent()
        agent_instance.messages = [{"role": "user", "content": "hello"}]
        agent_instance.total_input_tokens = 10
        agent_instance.total_output_tokens = 5

        try:
            resp1 = client.post(
                "/api/teams/messages",
                json=_make_activity(text="first message", conversation_id="conv-reuse"),
            )
            resp2 = client.post(
                "/api/teams/messages",
                json=_make_activity(text="second message", conversation_id="conv-reuse"),
            )

            assert resp1.status_code == 200
            assert resp2.status_code == 200

            # The session should exist with the teams- prefix
            session = session_store.get("teams-conv-reuse")
            assert session is not None
        finally:
            _restore_mock_agent(old)

    def test_different_conversations_get_different_sessions(self, client, session_store):
        """Different conversation IDs should create different sessions."""
        agent_instance, _, old = _setup_mock_agent()
        try:
            client.post(
                "/api/teams/messages",
                json=_make_activity(text="msg1", conversation_id="conv-A"),
            )
            client.post(
                "/api/teams/messages",
                json=_make_activity(text="msg2", conversation_id="conv-B"),
            )

            session_a = session_store.get("teams-conv-A")
            session_b = session_store.get("teams-conv-B")
            assert session_a is not None
            assert session_b is not None
            assert session_a.session_id != session_b.session_id
        finally:
            _restore_mock_agent(old)


class TestTeamsErrorHandling:
    """Test error handling in the Teams webhook."""

    def test_agent_error_returns_200_with_error_message(self, client):
        """When the agent raises an exception, the route should still return 200
        with a user-friendly error message."""
        mock_class = MagicMock(side_effect=RuntimeError("Agent broke"))
        old = sys.modules["server.core.agent"].SealineAgent
        sys.modules["server.core.agent"].SealineAgent = mock_class

        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="hello"),
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "error" in data["text"].lower() or "sorry" in data["text"].lower()
        finally:
            _restore_mock_agent(old)

    def test_rate_limit_error_message(self, client):
        """A RateLimitError should produce a rate-limit specific message."""

        class RateLimitError(Exception):
            pass

        mock_class = MagicMock(side_effect=RateLimitError("rate limited"))
        old = sys.modules["server.core.agent"].SealineAgent
        sys.modules["server.core.agent"].SealineAgent = mock_class

        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="hello"),
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "demand" in data["text"].lower() or "try again" in data["text"].lower()
        finally:
            _restore_mock_agent(old)


class TestTeamsWebHint:
    """Test that the web UI hint is appended for report-related keywords."""

    def test_report_keyword_appends_web_hint(self, client):
        """When the user asks about charts/reports, the reply should include a web UI hint."""
        agent_instance, _, old = _setup_mock_agent()
        agent_instance.send_message_sync.return_value = "Here is your data"
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="generate a chart of container volumes"),
            )
            data = resp.get_json()
            assert "web interface" in data["text"].lower()
        finally:
            _restore_mock_agent(old)

    def test_no_hint_for_normal_message(self, client):
        """Normal messages without report keywords should NOT get the web hint."""
        agent_instance, _, old = _setup_mock_agent()
        agent_instance.send_message_sync.return_value = "42 containers"
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="how many containers are active?"),
            )
            data = resp.get_json()
            assert "web interface" not in data["text"].lower()
        finally:
            _restore_mock_agent(old)


class TestTeamsResponseTruncation:
    """Test that overly long responses are truncated."""

    def test_long_response_truncated(self, client):
        """Responses longer than 4000 chars should be truncated."""
        agent_instance, _, old = _setup_mock_agent()
        agent_instance.send_message_sync.return_value = "x" * 5000
        try:
            resp = client.post(
                "/api/teams/messages",
                json=_make_activity(text="give me everything"),
            )
            data = resp.get_json()
            assert len(data["text"]) <= 4000
            assert "truncated" in data["text"]
        finally:
            _restore_mock_agent(old)
