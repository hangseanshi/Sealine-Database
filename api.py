"""
api.py — REST API wrapper around the Sealine Claude agent.

Exposes the ClaudeChat agent (from claude_desktop.py) via HTTP endpoints,
allowing users to create chat sessions and send prompts over a REST API.

Usage:
    uvicorn api:app --reload --port 8000

Endpoints:
    POST   /sessions              — create a new chat session
    GET    /sessions               — list active sessions
    POST   /sessions/{id}/chat     — send a message, get the agent's response
    GET    /sessions/{id}/history  — retrieve conversation history
    DELETE /sessions/{id}          — delete a session
"""

import os
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from claude_desktop import ClaudeChat, load_md_files

# Load environment variables from .env file
load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
DEFAULT_MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "8192"))
DEFAULT_SYSTEM = os.environ.get(
    "CLAUDE_SYSTEM_PROMPT",
    "You are Claude, a helpful AI assistant and data analyst "
    "for the Sealine shipping database. You have been given "
    "the database schema and reference documents as context.",
)
DOCS_DIR = os.environ.get("CLAUDE_DOCS_DIR", SCRIPT_DIR)
DISABLE_DB = os.environ.get("CLAUDE_DISABLE_DB", "").lower() in ("1", "true", "yes")
DISABLE_DOCS = os.environ.get("CLAUDE_DISABLE_DOCS", "").lower() in ("1", "true", "yes")

# ── Preload docs once at startup ──────────────────────────────────────────────
docs_text, docs_files = ("", []) if DISABLE_DOCS else load_md_files(DOCS_DIR)

# ── In-memory session store ──────────────────────────────────────────────────
sessions: dict[str, ClaudeChat] = {}


# ── Request / Response schemas ────────────────────────────────────────────────
class CreateSessionRequest(BaseModel):
    model: str | None = Field(default=None, description="Model ID override")
    system_prompt: str | None = Field(default=None, description="Custom system prompt")
    db_enabled: bool | None = Field(default=None, description="Enable SQL tool")


class CreateSessionResponse(BaseModel):
    session_id: str
    model: str
    db_enabled: bool
    docs_loaded: int


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message to send")


class ChatResponse(BaseModel):
    response: str
    session_id: str
    usage: dict


class SessionInfo(BaseModel):
    session_id: str
    model: str
    db_enabled: bool
    turns: int
    total_input_tokens: int
    total_output_tokens: int
    sql_calls: int


class HistoryMessage(BaseModel):
    role: str
    content: str


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_session(session_id: str) -> ClaudeChat:
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return sessions[session_id]


def _count_turns(chat: ClaudeChat) -> int:
    return sum(
        1 for m in chat.messages
        if m["role"] == "user"
        and not (
            isinstance(m["content"], list)
            and m["content"]
            and isinstance(m["content"][0], dict)
            and m["content"][0].get("type") == "tool_result"
        )
    )


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    yield
    sessions.clear()


app = FastAPI(
    title="Sealine Claude Agent API",
    description="REST API for the Sealine database Claude agent",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Frontend ──────────────────────────────────────────────────────────────
@app.get("/")
def serve_frontend():
    """Serve the chat interface."""
    return FileResponse(os.path.join(SCRIPT_DIR, "static", "index.html"))


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/sessions", response_model=CreateSessionResponse, status_code=201)
def create_session(req: CreateSessionRequest = None):
    """Create a new chat session."""
    if req is None:
        req = CreateSessionRequest()

    session_id = uuid.uuid4().hex[:12]
    chat = ClaudeChat(
        model=req.model or DEFAULT_MODEL,
        base_system=req.system_prompt or DEFAULT_SYSTEM,
        max_tokens=DEFAULT_MAX_TOKENS,
        docs_text=docs_text,
        docs_files=docs_files,
        db_enabled=(not DISABLE_DB) if req.db_enabled is None else req.db_enabled,
    )
    sessions[session_id] = chat

    return CreateSessionResponse(
        session_id=session_id,
        model=chat.model,
        db_enabled=chat.db_enabled,
        docs_loaded=len(chat.docs_files),
    )


@app.get("/sessions", response_model=list[SessionInfo])
def list_sessions():
    """List all active sessions."""
    return [
        SessionInfo(
            session_id=sid,
            model=chat.model,
            db_enabled=chat.db_enabled,
            turns=_count_turns(chat),
            total_input_tokens=chat.total_input_tokens,
            total_output_tokens=chat.total_output_tokens,
            sql_calls=chat.sql_calls,
        )
        for sid, chat in sessions.items()
    ]


@app.post("/sessions/{session_id}/chat", response_model=ChatResponse)
def chat(session_id: str, req: ChatRequest):
    """Send a message to the agent and get a response."""
    chat_instance = _get_session(session_id)

    try:
        response_text = chat_instance.send_api(req.message)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent error: {e}")

    return ChatResponse(
        response=response_text,
        session_id=session_id,
        usage={
            "total_input_tokens": chat_instance.total_input_tokens,
            "total_output_tokens": chat_instance.total_output_tokens,
            "cache_hits": chat_instance.cache_hits,
            "sql_calls": chat_instance.sql_calls,
        },
    )


@app.get("/sessions/{session_id}/history", response_model=list[HistoryMessage])
def get_history(session_id: str):
    """Get the conversation history for a session."""
    chat_instance = _get_session(session_id)
    history = []
    for msg in chat_instance.messages:
        content = msg["content"]
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Extract text from content blocks (skip tool_use/tool_result internals)
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    parts.append(f"[tool_result: {block.get('content', '')[:200]}]")
                elif hasattr(block, "type") and block.type == "text":
                    parts.append(block.text)
                elif hasattr(block, "type") and block.type == "tool_use":
                    parts.append(f"[tool_use: {block.name}]")
            text = "\n".join(parts) if parts else str(content)
        else:
            text = str(content)
        history.append(HistoryMessage(role=msg["role"], content=text))
    return history


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str):
    """Delete a chat session."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    del sessions[session_id]


# ── Static files (must be last so API routes take priority) ───────────────
app.mount("/static", StaticFiles(directory=os.path.join(SCRIPT_DIR, "static")), name="static")
