"""
Microsoft Teams bot webhook endpoint.

Blueprint: teams_bp
Prefix:    /api

Receives Bot Framework Activity JSON from Azure Bot Service, processes the
message through the SealineAgent in non-streaming (synchronous) mode, and
returns a Bot Framework Activity reply.

See PRD Section 11 for the full specification.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

teams_bp = Blueprint("teams", __name__, url_prefix="/api")

# Teams-specific system prompt (PRD 11.6).
TEAMS_SYSTEM_PROMPT = (
    "You are a data assistant for the Sealine shipping database, responding via "
    "Microsoft Teams. Keep responses concise (under 2000 characters). Use simple "
    "formatting. You can run SQL queries to answer data questions. "
    "Do NOT generate charts, PDFs, or Excel files. If the user asks for reports "
    "or visualizations, tell them to visit the web interface."
)

# Prefix used for Teams-specific session IDs so they do not collide with
# regular web sessions.
_TEAMS_SESSION_PREFIX = "teams-"


def _error(message: str, code: str, status: int):
    """Return a standardised error JSON response."""
    return jsonify({"error": message, "code": code, "status": status}), status


def _strip_mention(text: str) -> str:
    """
    Remove the ``<at>BotName</at>`` mention tag that Teams inserts when the
    bot is @mentioned in a channel.  Also strips any leading/trailing
    whitespace around the remaining text.
    """
    cleaned = re.sub(r"<at>.*?</at>", "", text, flags=re.IGNORECASE)
    return cleaned.strip()


def _get_or_create_teams_session(conversation_id: str):
    """
    Return an existing session for a Teams conversation, or create one.

    Sessions are keyed by ``teams-{conversation_id}`` so that follow-up
    messages in the same Teams thread share conversation context.
    """
    store = current_app.session_store
    session_id = f"{_TEAMS_SESSION_PREFIX}{conversation_id}"

    try:
        session = store.get(session_id)
        return session
    except KeyError:
        pass

    # Create a new session and assign the deterministic ID.
    session = store.create()
    old_id = session.session_id

    # Re-key the session in the store under the Teams-specific ID.
    # Since SessionStore.create() generates a UUID, we overwrite it with our
    # deterministic key so subsequent lookups succeed.
    session.session_id = session_id
    store.delete(old_id)

    # Put the session back under the new key.  We reach into the store's
    # internal dict directly — this is acceptable because the store is an
    # in-memory dict and we are the only caller.
    # (If SessionStore exposes a ``put`` method, prefer that.)
    if hasattr(store, "sessions"):
        store.sessions[session_id] = session
    elif hasattr(store, "_sessions"):
        store._sessions[session_id] = session

    logger.info(
        "Created Teams session %s for conversation %s",
        session_id,
        conversation_id,
    )
    return session


@teams_bp.route("/teams/messages", methods=["POST"])
def teams_webhook():
    """
    POST /api/teams/messages

    Receives a Bot Framework Activity from Azure Bot Service, processes the
    user message through the SealineAgent (non-streaming), and returns a
    Bot Framework Activity reply.
    """

    # ---------------------------------------------------------------------- #
    #  1. Parse the incoming Bot Framework Activity
    # ---------------------------------------------------------------------- #
    activity = request.get_json(silent=True)
    if activity is None:
        return _error(
            "Request body must be valid JSON",
            "INVALID_REQUEST",
            400,
        )

    # We only process ``message`` activities.
    activity_type = activity.get("type", "")
    if activity_type != "message":
        # Acknowledge non-message activities (e.g. conversationUpdate) with
        # an empty 200 so the Bot Service does not retry.
        logger.debug("Ignoring non-message activity type: %s", activity_type)
        return jsonify({}), 200

    raw_text = activity.get("text", "")
    message_text = _strip_mention(raw_text)

    if not message_text:
        return _error(
            "No message text found in the Activity",
            "INVALID_REQUEST",
            400,
        )

    conversation_id = activity.get("conversation", {}).get("id", "")
    if not conversation_id:
        return _error(
            "Missing conversation.id in the Activity",
            "INVALID_REQUEST",
            400,
        )

    # ---------------------------------------------------------------------- #
    #  2. Get or create a session for this Teams conversation
    # ---------------------------------------------------------------------- #
    session = _get_or_create_teams_session(conversation_id)

    # ---------------------------------------------------------------------- #
    #  3. Capture app context values before handing off to the agent
    # ---------------------------------------------------------------------- #
    cfg = current_app.config_obj
    docs_text = current_app.docs_text
    docs_files = current_app.docs_files

    try:
        import pyodbc  # noqa: F401
        db_enabled = True
    except ImportError:
        db_enabled = False

    # ---------------------------------------------------------------------- #
    #  4. Run the agent synchronously
    # ---------------------------------------------------------------------- #
    from server.core.agent import SealineAgent

    try:
        agent = SealineAgent(
            model=cfg.MODEL,
            system_prompt=TEAMS_SYSTEM_PROMPT,
            max_tokens=cfg.MAX_TOKENS,
            docs_text=docs_text,
            docs_files=docs_files,
            db_enabled=db_enabled,
            session_id=session.session_id,
            file_store_path=cfg.FILE_STORE_PATH,
            messages=list(session.messages),
        )

        response_text = agent.send_message_sync(message_text)

    except Exception as exc:
        logger.exception(
            "Teams agent error for conversation %s",
            conversation_id,
        )

        error_code = "AGENT_ERROR"
        exc_type_name = type(exc).__name__
        if "RateLimitError" in exc_type_name:
            error_code = "RATE_LIMITED"
            response_text = (
                "I'm currently experiencing high demand. "
                "Please try again in a moment."
            )
        elif "AuthenticationError" in exc_type_name:
            error_code = "CLAUDE_API_ERROR"
            response_text = (
                "There was an authentication issue with the AI service. "
                "Please contact the administrator."
            )
        elif "APIConnectionError" in exc_type_name:
            error_code = "CLAUDE_API_ERROR"
            response_text = (
                "I could not reach the AI service. "
                "Please try again shortly."
            )
        else:
            response_text = (
                "Sorry, I encountered an error processing your request. "
                "Please try again or contact the administrator."
            )

    # ---------------------------------------------------------------------- #
    #  5. Persist updated session state
    # ---------------------------------------------------------------------- #
    try:
        session.messages = agent.messages
        session.total_input_tokens = agent.total_input_tokens
        session.total_output_tokens = agent.total_output_tokens
        session.cache_hits = agent.cache_hits
        session.sql_calls = agent.sql_calls
        session.last_active = datetime.now(timezone.utc)
    except Exception:
        # If the agent errored before being created, we may not have an
        # ``agent`` object.  That is fine — session state stays as-is.
        pass

    # ---------------------------------------------------------------------- #
    #  6. If the response mentions reports/charts, append a web UI hint
    # ---------------------------------------------------------------------- #
    report_keywords = ["chart", "plot", "graph", "report", "excel", "pdf", "download", "file"]
    if any(kw in message_text.lower() for kw in report_keywords):
        host = cfg.HOST if cfg.HOST != "0.0.0.0" else "localhost"
        web_url = f"http://{host}:{cfg.PORT}"
        response_text += (
            f"\n\n_For reports, charts, and file downloads, visit the web "
            f"interface at {web_url}_"
        )

    # Truncate to Teams-friendly length (keep under ~4000 chars to be safe).
    if len(response_text) > 4000:
        response_text = response_text[:3950] + "\n\n_(response truncated)_"

    # ---------------------------------------------------------------------- #
    #  7. Build the Bot Framework Activity reply
    # ---------------------------------------------------------------------- #
    reply_activity = {
        "type": "message",
        "from": activity.get("recipient", {}),
        "recipient": activity.get("from", {}),
        "conversation": activity.get("conversation", {}),
        "replyToId": activity.get("id"),
        "text": response_text,
        "textFormat": "markdown",
    }

    return jsonify(reply_activity), 200
