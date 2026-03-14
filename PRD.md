# Product Requirements Document (PRD)

# Sealine Data Chat — Web API & Chat Interface

**Version:** 1.0
**Date:** March 11, 2026
**Status:** Draft

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Background & Current State](#2-background--current-state)
3. [Goals & Success Criteria](#3-goals--success-criteria)
4. [User Personas](#4-user-personas)
5. [System Architecture](#5-system-architecture)
6. [API Specification](#6-api-specification)
7. [Frontend — React Chat Interface](#7-frontend--react-chat-interface)
8. [Backend — Flask API Server](#8-backend--flask-api-server)
9. [Session & Context Management](#9-session--context-management)
10. [Report & File Generation](#10-report--file-generation)
11. [Microsoft Teams Integration](#11-microsoft-teams-integration)
12. [Infrastructure & Deployment](#12-infrastructure--deployment)
13. [Security & Network](#13-security--network)
14. [Error Handling](#14-error-handling)
15. [Data Flow Diagrams](#15-data-flow-diagrams)
16. [Technical Specifications](#16-technical-specifications)
17. [Out of Scope](#17-out-of-scope)
18. [Risks & Mitigations](#18-risks--mitigations)
19. [Milestones & Phasing](#19-milestones--phasing)

---

## 1. Executive Summary

This PRD defines the next phase of the Sealine Database Agent: transforming the existing terminal-based Claude chat (`claude_desktop.py`) into a **RESTful Flask API** with a **React-based web chat interface** and a **Microsoft Teams bot** integration.

Users inside the corporate firewall will be able to open a browser, start a new chat session, ask natural-language questions about Sealine shipping data, receive streamed AI responses backed by live SQL queries, and download generated reports (PDFs, Excel files, interactive plots) — all through a clean chat UI.

Additionally, users will be able to message a Teams bot for quick text-based data answers without leaving their collaboration tool.

**Key constraints:**
- V1 targets 1–5 concurrent internal users
- No authentication in V1 (firewall-only access control)
- No persistent chat history (session-scoped only, new chat = clean slate)
- No chat saving across sessions
- Teams bot provides text-only quick answers, directs to web UI for reports/files

---

## 2. Background & Current State

### What Exists Today

The Sealine Database Agent is a Python terminal application (`claude_desktop.py`, 489 lines) that provides an interactive Claude-powered chat with live database access. It runs on a single machine and is used by one developer at a time.

**Current capabilities:**
- **Live SQL execution** — Read-only queries (SELECT/WITH/EXEC) against the `searates` SQL Server database, capped at 500 rows, 30-second timeout
- **Natural-language data analysis** — Claude translates questions into SQL using cached schema documentation (5 markdown files loaded as system context with Anthropic's ephemeral prompt caching)
- **Streaming terminal chat** — Multi-turn REPL with real-time token streaming, ANSI colors, multi-line input, slash commands
- **Report generation** — Text (.txt), Excel (.xlsx with blue #1F4788 headers, frozen panes), email via Gmail API, interactive HTML maps (Google Maps API)
- **SSL bypass** — `httpx.Client(verify=False)` for Anthropic API calls through corporate proxy; standard ODBC for SQL Server

**Current file structure:**
```
Sealine-Database/
├── claude_desktop.py           # Main terminal chat agent
├── query_intransit.py          # Standalone in-transit container report
├── generate_map.py             # War zone container map generator
├── memory/                     # Cached context (auto-loaded as system prompt)
│   ├── MEMORY.md               # Quick reference & user preferences
│   ├── sealineDB_schema.md     # Full database schema (14+ tables)
│   ├── relationships.md        # Table join logic & FK mappings
│   ├── connections.md          # Database connection details
│   └── reports.md              # Saved report catalog
```

**Current database:** Microsoft SQL Server (`ushou102-exap1`, database `searates`) with core tables: `Sealine_Header`, `Sealine_Container`, `Sealine_Container_Event`, `Sealine_Locations`, `Sealine_Vessels`, `Sealine_Route`, `Sealine_Facilities`, plus request/API and reference tables.

### Why This Change Is Needed

1. **Accessibility** — Only one user at a time can use the terminal agent; a web API opens it to the team
2. **Richer output** — The terminal cannot render charts, plots, or downloadable files inline
3. **Teams integration** — Users want quick data answers without leaving their workflow
4. **Scalability foundation** — A REST API is the prerequisite for all future enhancements (permissions, persistence, mobile, etc.)

---

## 3. Goals & Success Criteria

### Primary Goals

| # | Goal | Success Metric |
|---|------|----------------|
| G1 | Convert terminal agent to a RESTful Flask API | All current `claude_desktop.py` capabilities available via HTTP endpoints |
| G2 | Build a React web chat interface | Users can open browser, start chats, send messages, see streamed responses |
| G3 | Support inline plots and downloadable reports | Matplotlib/Plotly charts render in chat; PDFs and Excel files are downloadable links |
| G4 | Maintain session context within a chat | Multi-turn conversation works; agent remembers earlier messages in the same session |
| G5 | Integrate with Microsoft Teams | Users can @mention the bot in Teams and get text-based data answers |

### Non-Goals (V1)

- No persistent chat history across sessions
- No chat saving or retrieval
- No user authentication or authorization
- No user-level table access permissions
- No rate limiting
- No mobile-specific UI
- No admin dashboard

---

## 4. User Personas

### Primary: Data Analyst (Web UI)

- Internal employee behind the corporate firewall
- Needs to query shipping data — shipment counts, container statuses, in-transit analysis, geographic breakdowns
- Wants to generate and download reports (Excel, PDF) and see visual charts
- Expects a conversational experience: ask follow-up questions, refine queries, explore data iteratively
- Opens a new chat for each task; does not need history between sessions

### Secondary: Operations Team Member (Teams)

- Wants quick answers without opening a separate application
- Asks simple questions in Microsoft Teams: "How many containers are in transit?", "What's the status of tracking number X?"
- If they need a report or chart, the bot directs them to the web UI
- Expects responses within 15–30 seconds

---

## 5. System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Corporate Firewall                             │
│                                                                        │
│  ┌──────────────┐     HTTP (SSE)     ┌────────────────────────────┐   │
│  │  React Chat   │ ◄────────────────► │  Flask API Server          │   │
│  │  (Browser)    │   localhost:8080   │  (Gunicorn + gevent)       │   │
│  └──────────────┘                    │                            │   │
│                                      │  ┌──────────────────────┐  │   │
│  ┌──────────────┐     HTTPS          │  │  Claude Agent Core   │  │   │
│  │  MS Teams     │ ◄──────────────►  │  │  (refactored from    │  │   │
│  │  (Bot)        │  Azure Bot Svc    │  │  claude_desktop.py)  │  │   │
│  └──────────────┘                    │  └──────────┬───────────┘  │   │
│                                      │             │              │   │
│                                      │  ┌──────────▼───────────┐  │   │
│                                      │  │  Session Store       │  │   │
│                                      │  │  (In-Memory dict)    │  │   │
│                                      │  └──────────────────────┘  │   │
│                                      │             │              │   │
│                                      │  ┌──────────▼───────────┐  │   │
│                                      │  │  File Store          │  │   │
│                                      │  │  (Temp dir, 24h TTL) │  │   │
│                                      │  └──────────────────────┘  │   │
│                                      └─────────┬──────────────────┘   │
│                                                 │                      │
│                              ┌──────────────────┼──────────────────┐   │
│                              │                  │                  │   │
│                      ┌───────▼───────┐  ┌───────▼───────┐         │   │
│                      │ SQL Server     │  │ Anthropic API  │         │
│                      │ ushou102-exap1 │  │ (verify=False) │         │
│                      │ DB: searates   │  │ claude-haiku   │         │
│                      └───────────────┘  └───────────────┘         │   │
│                                                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

### Component Breakdown

| Component | Technology | Responsibility |
|-----------|-----------|----------------|
| **Flask API** | Flask + Gunicorn (gevent workers) | REST endpoints, SSE streaming, session management, file serving |
| **Claude Agent Core** | Refactored `ClaudeChat` class | Anthropic API interaction, tool execution, agentic loop |
| **React Frontend** | React (served by Flask as static files) | Chat UI, SSE consumption, file download links, inline plots |
| **Session Store** | Python `dict` (in-memory) | Per-session message history, metadata, token tracking |
| **File Store** | Temp directory on filesystem | Generated PDFs, Excel files, plot images (24-hour TTL) |
| **Teams Bot** | Microsoft 365 Agents SDK | Receives Teams messages, calls Flask API, returns text responses |

---

## 6. API Specification

### Base URL

```
http://<hostname>:8080/api
```

Port 8080 is the default. Configurable via environment variable `PORT`.

### Endpoints

#### 6.1 `POST /api/sessions` — Create a New Chat Session

Creates a new session with a fresh conversation context. The agent loads the memory `.md` files as system context but has no prior conversation history.

**Request:**
```json
{}
```

**Response:**
```json
{
    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "created_at": "2026-03-11T14:30:00Z",
    "model": "claude-haiku-4-5",
    "db_enabled": true
}
```

**Behavior:**
- Generates a UUID-based session ID
- Initializes an empty message history list
- Loads all `memory/*.md` files as cached system context
- Initializes token and SQL call counters to zero
- Stores session object in the in-memory session store

---

#### 6.2 `POST /api/sessions/{session_id}/messages` — Send a Message (SSE Stream)

Sends a user message and streams the agent's response via Server-Sent Events.

**Request:**
```json
{
    "message": "How many containers are currently in transit?"
}
```

**Response:** `Content-Type: text/event-stream`

The response is an SSE stream with the following event types:

```
event: message_start
data: {"message_id": "msg_001", "session_id": "a1b2c3d4..."}

event: text_delta
data: {"delta": "Based on the current data, "}

event: text_delta
data: {"delta": "let me query the database for you."}

event: tool_start
data: {"tool": "execute_sql", "query": "SELECT COUNT(*) AS InTransitCount FROM Sealine_Header WHERE Status = 'IN_TRANSIT' AND DeletedDt IS NULL"}

event: tool_result
data: {"tool": "execute_sql", "result": "InTransitCount\n--------------\n4523\n\n(1 row)", "truncated": false}

event: text_delta
data: {"delta": "There are **4,523** containers currently in transit."}

event: file_generated
data: {"file_id": "f1a2b3c4", "filename": "transit_report.xlsx", "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "download_url": "/api/files/f1a2b3c4", "size_bytes": 15234}

event: plot_generated
data: {"file_id": "p1a2b3c4", "filename": "transit_chart.png", "type": "image/png", "url": "/api/files/p1a2b3c4"}

event: message_end
data: {"message_id": "msg_001", "usage": {"input_tokens": 3421, "output_tokens": 256, "cache_read_tokens": 8100, "sql_calls": 1}}
```

**SSE Event Types:**

| Event | Data Fields | Description |
|-------|-------------|-------------|
| `message_start` | `message_id`, `session_id` | Marks the beginning of a response |
| `text_delta` | `delta` | Incremental text chunk (streamed from Claude) |
| `thinking` | `content` | Claude's thinking block (if extended thinking is enabled) |
| `tool_start` | `tool`, `query` | Agent is executing a SQL query — display to user |
| `tool_result` | `tool`, `result`, `truncated` | SQL query result returned |
| `file_generated` | `file_id`, `filename`, `type`, `download_url`, `size_bytes` | A downloadable file was generated |
| `plot_generated` | `file_id`, `filename`, `type`, `url` | A plot/chart image was generated (display inline) |
| `error` | `error`, `code` | An error occurred |
| `message_end` | `message_id`, `usage` | Response complete, includes token usage stats |

**Behavior:**
- Appends user message to session's conversation history
- Runs the full agentic tool-use loop (may chain multiple Claude API calls + SQL queries)
- Streams each text delta, tool invocation, and file generation as SSE events
- Appends assistant response (including tool_use blocks) to session history
- Updates session token counters and SQL call count

**Error cases:**
- `404` — Session not found (expired or invalid)
- `400` — Empty message or missing `message` field
- `503` — Claude API unavailable or rate limited

---

#### 6.3 `GET /api/sessions/{session_id}` — Get Session Info

Returns session metadata and usage statistics. Does **not** return full message history (messages are internal to the agent).

**Response:**
```json
{
    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "created_at": "2026-03-11T14:30:00Z",
    "model": "claude-haiku-4-5",
    "message_count": 12,
    "usage": {
        "input_tokens": 45230,
        "output_tokens": 8900,
        "cache_hits": 5,
        "sql_calls": 7
    },
    "files_generated": [
        {
            "file_id": "f1a2b3c4",
            "filename": "transit_report.xlsx",
            "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "created_at": "2026-03-11T14:35:00Z"
        }
    ]
}
```

---

#### 6.4 `DELETE /api/sessions/{session_id}` — End a Session

Ends a session and cleans up associated resources (conversation history, generated files).

**Response:**
```json
{
    "status": "deleted",
    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

---

#### 6.5 `GET /api/files/{file_id}` — Download a Generated File

Serves a generated report, chart, or document for download.

**Response:** Binary file with appropriate `Content-Type` and `Content-Disposition` headers.

```
Content-Type: application/pdf
Content-Disposition: attachment; filename="transit_report.pdf"
```

**Supported file types:**

| Type | Content-Type | Display |
|------|-------------|---------|
| Excel (.xlsx) | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Download link |
| PDF (.pdf) | `application/pdf` | Download link |
| PNG chart (.png) | `image/png` | Inline in chat |
| HTML (interactive plot) | `text/html` | Inline iframe or link |

**Error cases:**
- `404` — File not found (expired, deleted, or invalid ID)
- `410` — File expired (past 24-hour TTL)

---

#### 6.6 `GET /api/health` — Health Check

**Response:**
```json
{
    "status": "healthy",
    "version": "1.0.0",
    "model": "claude-haiku-4-5",
    "db_connected": true,
    "uptime_seconds": 86400,
    "active_sessions": 3
}
```

---

#### 6.7 `POST /api/teams/messages` — Teams Bot Webhook

Receives incoming messages from Microsoft Teams via Azure Bot Service. This is the endpoint registered with the Bot Service.

**Request:** Standard Bot Framework Activity JSON (sent by Azure Bot Service).

**Response:** Bot Framework Activity response with text reply.

**Behavior:**
- Validates the incoming JWT token from Azure Bot Service
- Extracts user message text from the Activity
- Creates an ephemeral internal session (or reuses a Teams-specific session keyed by conversation ID)
- Runs the agentic loop (non-streaming, since Teams uses request-response)
- Returns text-only response
- If the query would benefit from files/charts, appends: _"For reports and charts, visit the web UI at http://\<hostname\>:8080"_
- Must respond within 15 seconds or send a "thinking..." proactive message first

---

## 7. Frontend — React Chat Interface

### 7.1 Overview

A functional MVP React application served as static files by the Flask API. No separate build server needed — the production build is placed in Flask's `static/` directory.

### 7.2 Pages & Layout

The application has a single page with a sidebar and main chat area.

```
┌─────────────────────────────────────────────────────────┐
│  Sealine Data Chat                              [+New]  │
├────────────┬────────────────────────────────────────────┤
│            │                                            │
│  Chat      │  Welcome! Ask me anything about the        │
│  Sessions  │  Sealine shipping database.                │
│            │                                            │
│ ┌────────┐ │  ┌──────────────────────────────────────┐  │
│ │ Chat 1 │ │  │ You: How many containers in transit? │  │
│ │ Active │ │  └──────────────────────────────────────┘  │
│ └────────┘ │                                            │
│ ┌────────┐ │  ┌──────────────────────────────────────┐  │
│ │ Chat 2 │ │  │ 🔧 SQL: SELECT COUNT(*)...           │  │
│ │        │ │  │                                      │  │
│ └────────┘ │  │ Agent: There are 4,523 containers    │  │
│            │  │ currently in transit.                 │  │
│            │  │                                      │  │
│            │  │ 📊 [Chart: Status Distribution]      │  │
│            │  │                                      │  │
│            │  │ 📎 transit_report.xlsx [Download]     │  │
│            │  └──────────────────────────────────────┘  │
│            │                                            │
│            │  ┌──────────────────────────────┐ [Send]   │
│            │  │ Type your message...         │          │
│            │  └──────────────────────────────┘          │
└────────────┴────────────────────────────────────────────┘
```

### 7.3 Chat Interface Components

#### Chat Message Types

The chat area renders different message types:

| Type | Rendering |
|------|-----------|
| **User message** | Right-aligned bubble with user text |
| **Agent text** | Left-aligned bubble with markdown-formatted text. Streamed character-by-character as SSE `text_delta` events arrive |
| **SQL execution** | Collapsible code block showing the query. Appears when `tool_start` event is received. Shows the result summary when `tool_result` arrives. Default state: collapsed |
| **Inline plot** | `<img>` tag rendering the chart PNG from `/api/files/{file_id}`. Appears inline in the conversation flow when `plot_generated` event is received |
| **Downloadable file** | File attachment card with filename, file type icon, file size, and a download button linking to `/api/files/{file_id}`. Appears when `file_generated` event is received |
| **Error** | Red-tinted error card with the error message |
| **Thinking** | Dimmed italic block showing Claude's reasoning (collapsible, default collapsed) |

#### Input Area

- Single-line text input with a Send button
- Enter key sends message; Shift+Enter for new line
- Input is disabled while the agent is responding (streaming state)
- Send button shows a loading spinner during streaming

#### Session Sidebar

- Lists all active sessions in the current browser tab (client-side state only)
- Each session shows a truncated first message as the title
- "[+ New Chat]" button creates a new session via `POST /api/sessions`
- Clicking a session switches to its chat view
- Sessions are **not persisted** — refreshing the browser loses the sidebar list (session data on the server remains until expiry or cleanup)

### 7.4 SSE Client Integration

The React frontend connects to the message endpoint using the `EventSource` API pattern (via `fetch` with `ReadableStream` for POST support, since native `EventSource` only supports GET).

```
User sends message
    → POST /api/sessions/{id}/messages (body: {message})
    → Response is text/event-stream
    → Client reads stream, parses SSE events
    → Each text_delta appended to current message bubble
    → tool_start/tool_result rendered as collapsible SQL block
    → file_generated/plot_generated rendered as attachment/image
    → message_end closes the stream, re-enables input
```

### 7.5 Markdown Rendering

Agent responses are rendered as Markdown with support for:
- **Bold**, *italic*, `inline code`
- Code blocks with syntax highlighting (SQL queries)
- Tables (for data results)
- Lists (bulleted and numbered)
- Links

Use a library like `react-markdown` with `rehype-highlight` for syntax highlighting.

### 7.6 Responsive Behavior

- MVP targets desktop browsers only (1280px+ width)
- Sidebar collapses on narrow screens (< 768px) with a hamburger toggle
- Chat area takes full width on mobile

### 7.7 Build & Serving

- React app is built with `npm run build` (Vite or Create React App)
- Built output placed in `server/static/` directory
- Flask serves the React app at `/` (catch-all route) and API at `/api/*`
- No separate frontend server needed in production

---

## 8. Backend — Flask API Server

### 8.1 Project Structure

```
Sealine-Database/
├── server/
│   ├── app.py                  # Flask application factory
│   ├── config.py               # Configuration (env vars, defaults)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── sessions.py         # /api/sessions endpoints
│   │   ├── messages.py         # /api/sessions/{id}/messages (SSE)
│   │   ├── files.py            # /api/files/{id} endpoint
│   │   ├── health.py           # /api/health endpoint
│   │   └── teams.py            # /api/teams/messages webhook
│   ├── core/
│   │   ├── __init__.py
│   │   ├── agent.py            # Refactored ClaudeChat class (from claude_desktop.py)
│   │   ├── sql_executor.py     # run_sql() with safety controls
│   │   ├── file_generator.py   # PDF, Excel, plot generation
│   │   └── context_loader.py   # Loads memory/*.md files
│   ├── sessions/
│   │   ├── __init__.py
│   │   └── store.py            # In-memory session store
│   ├── static/                 # React build output (served at /)
│   │   ├── index.html
│   │   ├── assets/
│   │   └── ...
│   ├── requirements.txt
│   └── gunicorn.conf.py        # Gunicorn configuration
├── client/                     # React source code
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── ChatArea.jsx
│   │   │   ├── MessageBubble.jsx
│   │   │   ├── SqlBlock.jsx
│   │   │   ├── FileBadge.jsx
│   │   │   ├── InlinePlot.jsx
│   │   │   ├── InputBar.jsx
│   │   │   └── Sidebar.jsx
│   │   ├── hooks/
│   │   │   └── useSSE.js       # SSE stream hook
│   │   ├── services/
│   │   │   └── api.js          # API client functions
│   │   └── styles/
│   ├── package.json
│   ├── vite.config.js
│   └── ...
├── memory/                     # Unchanged — system context docs
│   ├── MEMORY.md
│   ├── sealineDB_schema.md
│   ├── relationships.md
│   ├── connections.md
│   └── reports.md
├── claude_desktop.py           # Preserved (terminal mode still works)
├── PRD.md                      # This document
└── current_implementation.md   # Architecture reference
```

### 8.2 Flask Application Factory (`app.py`)

- Creates the Flask app
- Registers route blueprints (`sessions`, `messages`, `files`, `health`, `teams`)
- Loads configuration from environment variables
- Initializes the session store and context loader
- Configures CORS (if needed — same-origin serving avoids this)
- Serves React static files at `/` with a catch-all for client-side routing

### 8.3 Refactored Agent Core (`core/agent.py`)

The existing `ClaudeChat` class is refactored from a terminal-coupled class into a backend service:

**Changes from `claude_desktop.py`:**

| Aspect | Terminal Version | API Version |
|--------|-----------------|-------------|
| Output | `print()` to terminal | `yield` SSE events to a generator |
| Input | `input()` from stdin | JSON from HTTP request body |
| State | Single instance in main() | One instance per session, stored in session store |
| Streaming | ANSI colored text | SSE event stream (JSON payloads) |
| Tool display | Print SQL in yellow | `tool_start` / `tool_result` SSE events |
| File output | Write to filesystem | Generate to temp dir, return `file_id` |
| Errors | Print in red | `error` SSE event or HTTP error response |

The core agentic loop logic (send message → stream → detect tool_use → execute → loop) remains identical. The refactoring isolates I/O from logic.

**Key method signature:**

```python
def send_message(self, user_text: str) -> Generator[dict, None, None]:
    """
    Process a user message and yield SSE event dicts.

    Yields dicts like:
        {"event": "text_delta", "data": {"delta": "..."}}
        {"event": "tool_start", "data": {"tool": "execute_sql", "query": "..."}}
        {"event": "file_generated", "data": {"file_id": "...", ...}}
        {"event": "message_end", "data": {"usage": {...}}}
    """
```

### 8.4 SQL Executor (`core/sql_executor.py`)

Extracted from `run_sql()` in `claude_desktop.py`. Unchanged logic:

- Allowlist validation (SELECT, WITH, EXEC, EXECUTE only)
- PyODBC connection to `ushou102-exap1` / `searates`
- 500-row cap
- 30-second timeout
- Pipe-delimited text output returned to Claude

Additionally exposes a structured result for the API:

```python
def execute_sql(query: str) -> SqlResult:
    """Returns SqlResult with .text (for Claude) and .rows/.columns (for structured use)."""
```

### 8.5 File Generator (`core/file_generator.py`)

New module providing report and chart generation tools for the agent.

**Available as Claude tools:**

```python
TOOLS = [
    SQL_TOOL,           # execute_sql (existing)
    GENERATE_PLOT_TOOL, # generate_plot (new)
    GENERATE_PDF_TOOL,  # generate_pdf (new)
    GENERATE_EXCEL_TOOL # generate_excel (new)
]
```

See [Section 10: Report & File Generation](#10-report--file-generation) for details.

### 8.6 Gunicorn Configuration

```python
# gunicorn.conf.py
bind = "0.0.0.0:8080"
workers = 2              # Sufficient for 1-5 users
worker_class = "gevent"  # Required for SSE streaming
timeout = 300            # 5 minutes (long for complex queries)
keepalive = 65           # Keep SSE connections alive
```

**Why gevent?** Flask's default sync workers block during SSE streams. Gevent provides green threads that allow concurrent SSE connections without blocking other requests. For 1–5 users, 2 gevent workers are more than sufficient.

---

## 9. Session & Context Management

### 9.1 Session Lifecycle

```
[New Chat button] → POST /api/sessions → Session created (UUID)
        │
        ▼
[User sends message] → POST /api/sessions/{id}/messages
        │
        ▼
[Agent responds] → SSE stream → text, tools, files
        │
        ▼
[User sends another message] → Same session, full history sent to Claude
        │
        ▼
... (repeat) ...
        │
        ▼
[Session expires or user starts new chat] → Old session cleaned up
```

### 9.2 Session Object

```python
@dataclass
class Session:
    session_id: str                     # UUID
    created_at: datetime                # Creation timestamp
    last_active: datetime               # Updated on each message
    messages: list[dict]                # Full conversation history (Anthropic format)
    model: str                          # "claude-haiku-4-5"
    total_input_tokens: int             # Cumulative
    total_output_tokens: int            # Cumulative
    cache_hits: int                     # Prompt cache hit counter
    sql_calls: int                      # Tool call counter
    files: list[FileRecord]            # Generated files metadata
```

### 9.3 In-Memory Session Store

```python
class SessionStore:
    """Thread-safe in-memory session store."""

    sessions: dict[str, Session]       # Keyed by session_id

    def create() -> Session             # Create new session
    def get(session_id) -> Session      # Retrieve (or 404)
    def delete(session_id) -> None      # Remove session + cleanup files
    def cleanup_expired() -> None       # Remove sessions inactive > 2 hours
```

**Expiration:** Sessions are automatically cleaned up after 2 hours of inactivity. A background thread runs `cleanup_expired()` every 10 minutes.

**Limitations (V1, accepted):**
- Sessions are lost on server restart
- No horizontal scaling (all sessions in one process)
- Memory grows with conversation length — mitigated by the 2-hour expiration

### 9.4 Context Management

Each session's conversation context follows this structure:

```
System Prompt:
├── Base prompt: "You are Claude, a helpful AI data analyst for the Sealine shipping database..."
├── Tool instructions: "Use execute_sql for live data. Use generate_plot for charts..."
└── Cached context (cache_control: ephemeral):
    ├── memory/MEMORY.md
    ├── memory/sealineDB_schema.md
    ├── memory/relationships.md
    ├── memory/connections.md
    └── memory/reports.md

Messages:
├── User message 1
├── Assistant response 1 (may include tool_use blocks)
├── Tool results 1
├── User message 2
├── Assistant response 2
└── ... (grows with conversation)
```

**New chat = new session:** Every new session starts with zero message history. The memory `.md` files are the only carryover — they provide the agent with schema knowledge and behavioral instructions. This is the same behavior as the current terminal's `/clear` command.

**Token management:** Claude Haiku 4.5 supports a 200K-token context window. For a 1-5 user deployment, token costs are manageable. No automatic context compaction is implemented in V1. If a session's token count reaches approximately 150K input tokens, the API returns a warning in `message_end` events suggesting the user start a new chat.

---

## 10. Report & File Generation

### 10.1 New Agent Tools

In addition to the existing `execute_sql` tool, the agent gains three new tools:

#### `generate_plot`

Generates a chart/visualization from data.

```json
{
    "name": "generate_plot",
    "description": "Generate a chart or plot from data. Supports bar, line, scatter, pie, and heatmap chart types. Use matplotlib for static charts or plotly for interactive charts. The agent decides which library is more appropriate based on the data and request.",
    "input_schema": {
        "type": "object",
        "properties": {
            "plot_type": {
                "type": "string",
                "enum": ["bar", "line", "scatter", "pie", "heatmap", "histogram"],
                "description": "The type of chart to generate."
            },
            "title": {
                "type": "string",
                "description": "Chart title."
            },
            "data": {
                "type": "object",
                "description": "Chart data as JSON. Structure depends on plot_type. Example for bar: {\"labels\": [...], \"values\": [...]}."
            },
            "interactive": {
                "type": "boolean",
                "description": "If true, generate interactive Plotly HTML. If false, generate static matplotlib PNG.",
                "default": false
            }
        },
        "required": ["plot_type", "title", "data"]
    }
}
```

**Behavior:**
- Claude decides when to generate a plot based on the user's request and the data returned from SQL
- Static charts (matplotlib): Generated as PNG, returned as `plot_generated` SSE event, displayed inline in chat
- Interactive charts (plotly): Generated as self-contained HTML, returned as `file_generated` SSE event, openable in new tab

#### `generate_pdf`

Generates a PDF report from structured data.

```json
{
    "name": "generate_pdf",
    "description": "Generate a PDF report. Content is rendered from a simple template with a title, optional summary text, and a data table. Use this when the user explicitly asks for a PDF or a downloadable report.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Report title."
            },
            "summary": {
                "type": "string",
                "description": "Optional summary paragraph shown above the data table."
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column headers for the data table."
            },
            "rows": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
                "description": "Table rows. Each row is an array of string values."
            },
            "filename": {
                "type": "string",
                "description": "Output filename (without extension)."
            }
        },
        "required": ["title", "columns", "rows"]
    }
}
```

**Implementation:** Uses WeasyPrint to convert an HTML template to PDF. The template provides:
- Report title and generation timestamp
- Optional summary text
- Data table with alternating row colors
- Clean, professional styling (no custom branding required for V1)

#### `generate_excel`

Generates a formatted Excel file. Same styling as existing `openpyxl` output.

```json
{
    "name": "generate_excel",
    "description": "Generate a formatted Excel (.xlsx) report with blue header row, frozen top row, and auto-sized columns. Use when the user asks for a spreadsheet or Excel export.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Sheet name / report title."
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column headers."
            },
            "rows": {
                "type": "array",
                "items": {"type": "array"},
                "description": "Data rows."
            },
            "filename": {
                "type": "string",
                "description": "Output filename (without extension)."
            }
        },
        "required": ["title", "columns", "rows"]
    }
}
```

**Styling:** Blue header (#1F4788, white text), frozen pane at row 1, auto-width columns — matching the existing preference from `MEMORY.md`.

### 10.2 File Storage & Lifecycle

| Aspect | Detail |
|--------|--------|
| Location | `{server_root}/tmp/files/` |
| Naming | `{file_id}_{original_filename}` where `file_id` is a short UUID |
| TTL | 24 hours from creation |
| Cleanup | Background thread runs every hour, deletes expired files |
| Max size | No explicit limit (V1). Typical reports are < 5MB |
| Association | Files are linked to their session via `session.files` list |

### 10.3 How Files Appear in Chat

**Plots (PNG):** Rendered inline as images in the chat bubble. The agent generates the plot, saves it to the file store, and emits a `plot_generated` SSE event. The React frontend inserts an `<img>` tag.

**PDFs and Excel files:** Rendered as a file attachment card below the agent's text response. The card shows the filename, file size, and a download button. Clicking the button triggers a GET request to `/api/files/{file_id}`.

**Example chat flow:**
```
User: "Show me a breakdown of container status and give me a PDF report"

Agent: Let me query the data...

[SQL Block: SELECT Status, COUNT(*) FROM Sealine_Container GROUP BY Status]

Here's the container status breakdown:
| Status     | Count |
|------------|-------|
| IN_TRANSIT | 4,523 |
| DELIVERED  | 8,201 |
| UNKNOWN    | 312   |

[Inline Chart: Pie chart of status distribution]

📎 container_status_report.pdf (12.4 KB) [Download]
```

---

## 11. Microsoft Teams Integration

### 11.1 Overview

A Microsoft Teams bot that allows users to ask quick data questions directly in Teams. The bot provides text-only responses and directs users to the web UI for reports, charts, and file downloads.

### 11.2 Architecture

```
MS Teams Client
      │
      ▼
Azure Bot Service (cloud)
      │ HTTPS POST (Bot Framework Activity)
      ▼
Flask API: POST /api/teams/messages
      │
      ├── Validate JWT token from Azure Bot Service
      ├── Extract message text from Activity
      ├── Create/reuse ephemeral session
      ├── Run agent (non-streaming, text-only tools)
      └── Return text response as Activity reply
      │
      ▼
Azure Bot Service → MS Teams Client
```

### 11.3 Bot Capabilities

| Capability | Supported | Notes |
|------------|-----------|-------|
| Text Q&A | ✅ | "How many containers in transit?", "Status of TRACK123?" |
| SQL queries | ✅ | Agent runs SQL internally, returns text summary |
| Charts/plots | ❌ | Responds: "For charts, visit the web UI at ..." |
| File generation | ❌ | Responds: "For reports, visit the web UI at ..." |
| 1:1 chat | ✅ | Personal bot conversation |
| Channel mentions | ✅ | @mention the bot in a channel |
| Proactive messaging | ❌ (V1) | Bot only responds to messages |

### 11.4 Teams-Specific Constraints

- **15-second response limit:** Azure Bot Service expects a response within ~15 seconds. For complex queries that take longer:
  1. Immediately respond with a "thinking" message: _"Querying the database, one moment..."_
  2. Process the query asynchronously
  3. Send the final answer via a proactive message update

- **No streaming:** Teams uses a request-response pattern. The bot cannot stream tokens. The full response is composed server-side before sending.

- **Text formatting:** Teams supports a subset of Markdown. Keep responses simple: bold, italic, code blocks, tables. Avoid complex HTML.

- **Session behavior:** Each Teams conversation (1:1 or channel thread) maps to a session. The session maintains context for follow-up questions within the same thread. Starting a new thread starts a new session.

### 11.5 Prerequisites

- Azure subscription with Azure Bot Service
- Azure AD app registration (for bot identity and token validation)
- Microsoft 365 Agents SDK (`microsoft-agents-*` Python packages)
- Bot registered in Teams Admin Center for internal distribution

### 11.6 System Prompt Override for Teams

The Teams bot uses a modified system prompt:

```
You are a data assistant for the Sealine shipping database, responding via Microsoft Teams.
Keep responses concise (under 2000 characters). Use simple formatting.
You can run SQL queries to answer data questions.
Do NOT generate charts, PDFs, or Excel files. If the user asks for reports or visualizations,
tell them to visit the web interface at http://<hostname>:8080.
```

---

## 12. Infrastructure & Deployment

### 12.1 Target Environment

| Aspect | Detail |
|--------|--------|
| OS | Linux (on-prem, to be provisioned) |
| Python | 3.10+ |
| WSGI Server | Gunicorn with gevent workers |
| Port | 8080 (configurable via `PORT` env var) |
| Network | Internal corporate network, behind firewall |
| Outbound access | Anthropic API (api.anthropic.com) via corporate proxy |
| Inbound access | Internal users on corporate network only |
| SQL Server | `ushou102-exap1` (must be reachable from Linux host) |

### 12.2 Server Prerequisites

The following must be installed on the Linux host:

```bash
# System packages
python3.10+
pip
ODBC Driver 17 for SQL Server (unixODBC + msodbcsql17)
libffi-dev, libcairo2-dev, libpango1.0-dev  # For WeasyPrint PDF generation

# Python packages (requirements.txt)
flask>=3.0
gunicorn>=21.2
gevent>=24.2
anthropic>=0.40
httpx>=0.27
pyodbc>=5.1
openpyxl>=3.1
matplotlib>=3.8
plotly>=5.18
weasyprint>=62.0
```

### 12.3 Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...          # Claude API key

# Optional (with defaults)
PORT=8080                              # API listen port
HOST=0.0.0.0                          # Bind address
WORKERS=2                             # Gunicorn worker count
MODEL=claude-haiku-4-5                 # Default Claude model
DB_SERVER=ushou102-exap1               # SQL Server host
DB_NAME=searates                       # Database name
DB_USER=sean                           # Database username
DB_PASSWORD=4peiling                   # Database password
SESSION_TTL_HOURS=2                    # Session expiration
FILE_TTL_HOURS=24                      # Generated file expiration
MEMORY_DIR=./memory                    # Path to memory/*.md files
```

### 12.4 Startup

```bash
# Production
gunicorn -c gunicorn.conf.py server.app:create_app()

# Development
python -m flask --app server.app run --debug --port 8080
```

### 12.5 Process Management

For production, use `systemd` to manage the Gunicorn process:

```ini
[Unit]
Description=Sealine Data Chat API
After=network.target

[Service]
User=sealine
WorkingDirectory=/opt/sealine-data-chat
EnvironmentFile=/opt/sealine-data-chat/.env
ExecStart=/opt/sealine-data-chat/venv/bin/gunicorn -c gunicorn.conf.py server.app:create_app()
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 13. Security & Network

### 13.1 V1 Security Model

| Layer | Approach |
|-------|----------|
| **Network** | Firewall-only access control. API is accessible only from the corporate network. |
| **Authentication** | None in V1. All requests are anonymous. |
| **Authorization** | None in V1. All users can query all tables. |
| **SQL injection** | Agent-generated queries only. First-word allowlist (SELECT/WITH/EXEC). No user-supplied SQL passed directly. |
| **Data exposure** | Read-only queries only. No INSERT/UPDATE/DELETE. 500-row cap prevents data dumps. |
| **API key** | `ANTHROPIC_API_KEY` stored as environment variable, never sent to clients. |
| **DB credentials** | Stored in environment variables, never sent to clients. |

### 13.2 SSL / TLS

| Connection | SSL Handling |
|------------|-------------|
| Client ↔ Flask API | HTTP (no TLS). Acceptable for internal-only deployment behind firewall. |
| Flask API ↔ Anthropic | HTTPS with `verify=False` (bypasses corporate proxy's SSL inspection). Isolated to the Anthropic `httpx` client only. |
| Flask API ↔ SQL Server | Standard ODBC (no explicit TLS). Internal network routing. |

### 13.3 CORS

Not required in V1. The React frontend is served by the same Flask server (same origin), so all API calls are same-origin requests. No CORS headers needed.

If a separate frontend host is used in the future, add `Flask-CORS` with an explicit origin whitelist.

---

## 14. Error Handling

### 14.1 API Error Responses

All non-SSE error responses follow a consistent JSON format:

```json
{
    "error": "Human-readable error message",
    "code": "ERROR_CODE",
    "status": 404
}
```

| HTTP Status | Code | Scenario |
|-------------|------|----------|
| 400 | `INVALID_REQUEST` | Missing or empty message, malformed JSON |
| 404 | `SESSION_NOT_FOUND` | Session ID doesn't exist or has expired |
| 404 | `FILE_NOT_FOUND` | File ID doesn't exist or has expired |
| 500 | `AGENT_ERROR` | Unhandled exception in the agent core |
| 502 | `CLAUDE_API_ERROR` | Anthropic API returned an error |
| 503 | `RATE_LIMITED` | Anthropic rate limit hit |
| 503 | `DB_UNAVAILABLE` | SQL Server connection failed |

### 14.2 SSE Error Events

During a streaming response, errors are sent as SSE events rather than HTTP status codes (since the HTTP status has already been sent as 200):

```
event: error
data: {"error": "SQL Server connection timed out", "code": "DB_TIMEOUT", "recoverable": true}
```

The `recoverable` flag indicates whether the agent can continue (e.g., it may retry the query or answer from context).

### 14.3 Agent Self-Correction

The existing behavior is preserved: when a SQL query fails, the error text is returned to Claude as a tool result. Claude then self-corrects (rewrites the query, tries a different approach, or explains the error to the user). This happens transparently within the agentic loop.

---

## 15. Data Flow Diagrams

### 15.1 Web Chat — Full Message Flow

```
React Frontend                   Flask API                    Claude API           SQL Server
     │                              │                             │                    │
     │  POST /api/sessions          │                             │                    │
     │─────────────────────────────►│                             │                    │
     │  {session_id: "abc123"}      │                             │                    │
     │◄─────────────────────────────│                             │                    │
     │                              │                             │                    │
     │  POST /api/sessions/abc123/messages                        │                    │
     │  {message: "show transit"}   │                             │                    │
     │─────────────────────────────►│                             │                    │
     │                              │                             │                    │
     │  SSE: message_start          │                             │                    │
     │◄─────────────────────────────│                             │                    │
     │                              │  messages.stream()          │                    │
     │                              │────────────────────────────►│                    │
     │  SSE: text_delta             │  text delta                 │                    │
     │◄─────────────────────────────│◄────────────────────────────│                    │
     │  SSE: text_delta             │  text delta                 │                    │
     │◄─────────────────────────────│◄────────────────────────────│                    │
     │                              │  stop_reason: tool_use      │                    │
     │                              │◄────────────────────────────│                    │
     │  SSE: tool_start             │                             │                    │
     │◄─────────────────────────────│                             │                    │
     │                              │  execute SQL                │                    │
     │                              │────────────────────────────────────────────────►│
     │                              │  result rows                │                    │
     │                              │◄────────────────────────────────────────────────│
     │  SSE: tool_result            │                             │                    │
     │◄─────────────────────────────│                             │                    │
     │                              │  messages.stream() (with tool_result)           │
     │                              │────────────────────────────►│                    │
     │  SSE: text_delta             │  text delta                 │                    │
     │◄─────────────────────────────│◄────────────────────────────│                    │
     │                              │  stop_reason: end_turn      │                    │
     │                              │◄────────────────────────────│                    │
     │  SSE: message_end            │                             │                    │
     │◄─────────────────────────────│                             │                    │
     │                              │                             │                    │
```

### 15.2 Teams Bot — Message Flow

```
MS Teams          Azure Bot Service         Flask API              Claude API       SQL Server
  │                     │                      │                      │                │
  │  User message       │                      │                      │                │
  │────────────────────►│                      │                      │                │
  │                     │  POST /api/teams/messages                   │                │
  │                     │─────────────────────►│                      │                │
  │                     │                      │  Validate JWT        │                │
  │                     │                      │  Extract text        │                │
  │                     │                      │  Create session      │                │
  │                     │                      │                      │                │
  │                     │                      │  messages.create()   │                │
  │                     │                      │─────────────────────►│                │
  │                     │                      │  tool_use            │                │
  │                     │                      │◄─────────────────────│                │
  │                     │                      │  execute SQL         │                │
  │                     │                      │─────────────────────────────────────►│
  │                     │                      │  result              │                │
  │                     │                      │◄─────────────────────────────────────│
  │                     │                      │  messages.create()   │                │
  │                     │                      │─────────────────────►│                │
  │                     │                      │  end_turn (text)     │                │
  │                     │                      │◄─────────────────────│                │
  │                     │  Response Activity    │                      │                │
  │                     │◄─────────────────────│                      │                │
  │  Bot reply          │                      │                      │                │
  │◄────────────────────│                      │                      │                │
  │                     │                      │                      │                │
```

---

## 16. Technical Specifications

### 16.1 Model Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Model | `claude-haiku-4-5` | Fast, cost-effective for data queries. Sufficient for SQL generation. |
| Max output tokens | 8,192 | Same as current terminal agent |
| Prompt caching | Ephemeral (on system context) | Reduces input tokens on subsequent turns by caching the ~10KB of memory docs |
| Temperature | Default (1.0) | Standard for conversational + analytical tasks |
| Tools | `execute_sql`, `generate_plot`, `generate_pdf`, `generate_excel` | Core agent capabilities |

### 16.2 Database Configuration

| Parameter | Value |
|-----------|-------|
| Driver | ODBC Driver 17 for SQL Server |
| Server | `ushou102-exap1` (configurable via `DB_SERVER` env var) |
| Database | `searates` (configurable via `DB_NAME` env var) |
| Max rows | 500 per query |
| Timeout | 30 seconds per query |
| Allowed operations | SELECT, WITH, EXEC, EXECUTE |
| Connection pooling | Not implemented in V1 (new connection per query) |

### 16.3 Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Time to first token (web) | < 2 seconds | From message send to first `text_delta` |
| Simple query end-to-end | < 10 seconds | Single SQL query + text response |
| Complex query end-to-end | < 45 seconds | Multiple chained SQL queries + analysis |
| SSE connection stability | No drops for 5+ minutes | Gevent keepalive handles this |
| File generation (Excel) | < 3 seconds | For typical report sizes (< 500 rows) |
| File generation (PDF) | < 5 seconds | WeasyPrint HTML-to-PDF rendering |
| Plot generation | < 3 seconds | matplotlib PNG or plotly HTML |
| Teams response | < 15 seconds | Must reply before Azure Bot Service timeout |

### 16.4 Dependencies

```
# Core
flask>=3.0
gunicorn>=21.2
gevent>=24.2
anthropic>=0.40
httpx>=0.27

# Database
pyodbc>=5.1

# Report generation
openpyxl>=3.1         # Excel
weasyprint>=62.0      # PDF
matplotlib>=3.8       # Static charts
plotly>=5.18          # Interactive charts

# Teams bot
microsoft-agents-*    # Microsoft 365 Agents SDK (exact packages TBD based on GA release)

# Frontend (npm)
react>=18
react-dom>=18
react-markdown
rehype-highlight
vite
```

---

## 17. Out of Scope

The following are explicitly **not** included in this PRD and are deferred to future phases:

| Item | Reason |
|------|--------|
| **User authentication / login** | Firewall provides sufficient access control for V1. |
| **User ID / table-level permissions** | Long-term goal, deferred per stakeholder decision. |
| **Persistent chat history** | V1 is session-scoped only. No database storage of conversations. |
| **Chat saving / retrieval** | Users cannot revisit old chats. Each session is ephemeral. |
| **Rate limiting** | Trust-based for 1-5 internal users in V1. |
| **Token budget per session** | No hard limits on token usage per session. |
| **Model selection UI** | Fixed to Haiku. No user-facing model picker. |
| **Admin dashboard** | No usage analytics, user management, or system monitoring UI. |
| **Mobile-responsive UI** | Desktop-first MVP. Basic collapse behavior only. |
| **Email delivery from web UI** | Existing Gmail API integration is not ported to V1 web. Users download files instead. |
| **Connection pooling** | New PyODBC connection per query. Acceptable for 1-5 users. |
| **Database for sessions** | Redis or SQL-based session persistence is deferred. |
| **Horizontal scaling** | Single server, single process. No load balancing. |

---

## 18. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | **Server not provisioned in time** | Medium | High | Begin development locally. Use Docker for local testing. Document all Linux dependencies for handoff to IT. |
| R2 | **ODBC Driver 17 unavailable on Linux** | Low | High | Use the `msodbcsql17` package from Microsoft's Linux repository. Test connectivity early. Fallback: FreeTDS driver. |
| R3 | **Corporate proxy blocks SSE streams** | Low | Medium | SSE runs over standard HTTP. Test with a curl SSE client from a workstation early. Fallback: long-polling endpoint. |
| R4 | **WeasyPrint system dependency issues** | Medium | Low | WeasyPrint requires `cairo`, `pango`, and `gdk-pixbuf`. Document `apt-get` packages. Fallback: use ReportLab (pure Python) for PDF. |
| R5 | **Azure Bot Service provisioning delays** | Medium | Medium | Teams integration can be delivered independently after web UI. No dependency between them. |
| R6 | **Conversation token growth causes high costs** | Low | Medium | Monitor with `/api/sessions/{id}` usage stats. Add warning at 150K tokens. 2-hour session TTL naturally limits growth. |
| R7 | **Memory leak from in-memory sessions** | Low | Medium | Session cleanup thread runs every 10 minutes. 2-hour TTL. Monitor process memory. |
| R8 | **Anthropic API outage** | Low | High | Return clear error to user. Agent cannot function without API. No local fallback. |

---

## 19. Milestones & Phasing

### Phase 1: Core API + Web Chat (Weeks 1–3)

| Week | Deliverable |
|------|------------|
| **Week 1** | Refactor `claude_desktop.py` into Flask API. Extract `ClaudeChat` into `core/agent.py`. Implement `/api/sessions`, `/api/sessions/{id}/messages` (SSE), `/api/health`. In-memory session store. Test with `curl` and Postman. |
| **Week 2** | Build React chat frontend. Chat UI with message bubbles, streaming text, SQL blocks. Sidebar with session list. Input bar. Connect to SSE endpoint. Serve from Flask. |
| **Week 3** | File generation tools (`generate_plot`, `generate_pdf`, `generate_excel`). File download endpoint. Inline plot rendering and file attachment cards in chat. End-to-end testing. |

### Phase 2: Teams Bot (Weeks 4–5)

| Week | Deliverable |
|------|------------|
| **Week 4** | Azure Bot Service setup. App registration. Teams bot webhook endpoint. Non-streaming agent execution for Teams. Text-only responses. |
| **Week 5** | Teams bot testing. Handle 15-second timeout with proactive messaging. Internal Teams app distribution. Documentation. |

### Phase 3: Polish & Deploy (Week 6)

| Week | Deliverable |
|------|------------|
| **Week 6** | Linux server provisioning and deployment. Gunicorn + systemd setup. End-to-end testing on production server. Bug fixes and performance tuning. |

### Definition of Done

- [ ] Flask API serves all defined endpoints
- [ ] React chat UI renders streamed responses, SQL blocks, inline plots, and file download cards
- [ ] New chat sessions start with clean context (memory docs only)
- [ ] Session context is maintained for multi-turn conversations
- [ ] Agent can generate and serve Excel, PDF, and chart files
- [ ] Teams bot responds to text queries within 15 seconds
- [ ] Teams bot directs to web UI for report/file requests
- [ ] System runs on Linux with Gunicorn + gevent
- [ ] `claude_desktop.py` (terminal mode) continues to work independently
- [ ] All existing agent capabilities (SQL, schema awareness, tool chaining) preserved

---

*End of PRD*
