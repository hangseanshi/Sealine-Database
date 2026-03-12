# Unit Test Results -- Flask API Routes & App Factory

**Date:** 2026-03-11
**Tester:** QA Engineer (automated)
**Python:** 3.9.6
**Framework:** pytest 8.4.2, Flask 3.1.3
**Result:** ALL 117 TESTS PASSED

---

## Summary

| Test File                     | Tests | Passed | Failed | Coverage Area                |
|-------------------------------|-------|--------|--------|------------------------------|
| `test_app.py`                 | 24    | 24     | 0      | App factory, blueprints, config |
| `test_routes_sessions.py`     | 18    | 18     | 0      | Session CRUD endpoints       |
| `test_routes_messages.py`     | 17    | 17     | 0      | Message streaming (SSE)      |
| `test_routes_files.py`        | 14    | 14     | 0      | File download endpoint       |
| `test_routes_health.py`       | 14    | 14     | 0      | Health check endpoint        |
| `test_routes_teams.py`        | 30    | 30     | 0      | Teams webhook endpoint       |
| **TOTAL**                     | **117** | **117** | **0** |                              |

---

## Test Details by Module

### 1. App Factory (`test_app.py`) -- 24 tests

**TestCreateAppViaFixture (13 tests)**
- App is Flask instance
- All 5 blueprints registered (sessions, messages, files, health, teams)
- Exactly 5 blueprints total
- `session_store` accessible as direct attribute
- `docs_text` accessible as direct attribute
- `docs_files` accessible as direct attribute
- `config_obj` accessible as direct attribute
- SESSION_STORE in app.config dict
- CONTEXT_TEXT in app.config dict
- CONTEXT_FILES in app.config dict
- ANTHROPIC_CLIENT in app.config dict
- FILE_STORE_PATH in app.config dict
- SEALINE_CONFIG in app.config dict

**TestStaticServing (3 tests)**
- /api/health accessible (not caught by static fallback)
- /api/sessions accessible (POST returns 201)
- Static URL path configured as /static

**TestBlueprintURLPrefixes (5 tests)**
- All 5 blueprints use /api URL prefix

**TestAppConfiguration (3 tests)**
- TESTING flag set for test apps
- Session store on app matches fixture
- Config model is "claude-haiku-4-5"

### 2. Sessions Route (`test_routes_sessions.py`) -- 18 tests

**TestCreateSession (6 tests)**
- POST /api/sessions returns 201
- Response contains session_id (non-empty string)
- Response contains created_at (valid ISO timestamp)
- Response contains model name ("claude-haiku-4-5")
- Response contains db_enabled (boolean)
- Multiple sessions get unique IDs

**TestGetSession (9 tests)**
- GET existing session returns 200
- Response includes session_id, created_at, model, message_count, usage, files_generated
- Usage object contains input_tokens, output_tokens, cache_hits, sql_calls
- New session has message_count 0
- Counts user messages with string content correctly
- Excludes tool_result pseudo-user messages from count
- Non-existent session returns 404
- 404 response has SESSION_NOT_FOUND code and status 404
- Session with files returns files_generated with correct fields

**TestDeleteSession (6 tests)**
- DELETE existing session returns 200
- Response body has status "deleted" and session_id
- Session removed from store after deletion (GET returns 404)
- Non-existent session returns 404
- 404 response has SESSION_NOT_FOUND code
- Associated files cleaned up from disk

### 3. Messages Route (`test_routes_messages.py`) -- 17 tests

**TestSendMessageValidation (7 tests)**
- Missing JSON body returns 400
- Empty message returns 400
- Missing message field returns 400
- Whitespace-only message returns 400
- Non-existent session returns 404
- 404 has SESSION_NOT_FOUND code
- Non-string message returns 400

**TestSendMessageStreaming (8 tests)**
- Valid message returns 200 with text/event-stream content type
- Stream begins with message_start event
- Stream ends with message_end event
- message_end contains usage stats (input_tokens, output_tokens)
- message_start contains message_id and session_id
- text_delta events forwarded from agent to SSE stream
- Agent's own message_start/message_end filtered out (only wrapper's emitted)
- Response headers include Cache-Control: no-cache and X-Accel-Buffering: no

**TestSendMessageErrorHandling (5 tests)**
- Agent RuntimeError emits error SSE event with AGENT_ERROR code
- Error still emits message_end event (stream completion)
- RateLimitError produces RATE_LIMITED code
- AuthenticationError produces CLAUDE_API_ERROR code
- Error events include recoverable: false field

### 4. Files Route (`test_routes_files.py`) -- 14 tests

**TestDownloadFile (5 tests)**
- Existing file returns 200
- Response body contains actual file content
- Non-existent file returns 404
- 404 has FILE_NOT_FOUND code and status 404
- File record exists but disk file missing returns 410 (expired)

**TestFileContentTypes (5 tests)**
- XLSX returns correct MIME type
- PDF returns application/pdf
- PNG returns image/png
- CSV returns text/csv
- HTML returns text/html

**TestFileDisposition (4 tests)**
- PNG served inline (no Content-Disposition: attachment)
- XLSX served as attachment (download)
- Attachment header contains original filename
- Files findable across sessions (not just the first)

### 5. Health Route (`test_routes_health.py`) -- 14 tests

**TestHealthEndpoint (8 tests)**
- GET /api/health returns 200
- Response is JSON content type
- Contains status: "healthy"
- Contains version: "1.0.0"
- Contains model matching config ("claude-haiku-4-5")
- Contains db_connected (boolean)
- Contains uptime_seconds (non-negative number)
- Contains active_sessions (integer)

**TestHealthDBConnection (3 tests)**
- db_connected true when DB check succeeds
- db_connected false when DB check fails
- Health still returns 200 when DB is down

**TestHealthActiveSessions (3 tests)**
- Zero sessions initially
- Count increases after session creation
- Count decreases after session deletion

**TestHealthJSONStructure (2 tests)**
- Response contains exactly the 6 expected keys
- No unexpected extra fields

### 6. Teams Route (`test_routes_teams.py`) -- 30 tests

**TestTeamsWebhookValidation (6 tests)**
- Valid message activity returns 200
- Non-message activity (conversationUpdate) returns 200 with empty JSON
- Invalid JSON returns 400
- Empty text returns 400 with INVALID_REQUEST code
- Missing conversation.id returns 400
- Missing conversation object returns 400

**TestTeamsReplyFormat (5 tests)**
- Reply has type "message"
- Reply contains agent's response text
- Reply specifies textFormat "markdown"
- Reply swaps from/recipient (bot replies as recipient)
- Reply includes replyToId referencing original activity

**TestMentionStripping (4 tests)**
- `<at>BotName</at>` tag stripped from message
- Multiple `<at>` tags all stripped
- Plain text passes through unchanged
- Mention-only text (no actual content) returns 400

**TestTeamsSessionManagement (2 tests)**
- Same conversation ID reuses session (teams- prefix)
- Different conversation IDs get different sessions

**TestTeamsErrorHandling (2 tests)**
- Agent error returns 200 with user-friendly error message
- RateLimitError produces rate-limit specific message

**TestTeamsWebHint (2 tests)**
- Report keywords (chart, plot, report, etc.) append web UI hint
- Normal messages do NOT get web hint

**TestTeamsResponseTruncation (1 test)**
- Responses >4000 chars truncated with "truncated" marker

---

## Test Infrastructure

### Shared Fixtures (`conftest.py`)
- **TestSessionStore**: Subclass of SessionStore that skips background cleanup thread
- **Fake pyodbc module**: Injected into sys.modules for DB-related tests
- **Fake server.core.agent module**: Injected into sys.modules to avoid Python 3.9 import chain issues with `Config | None` syntax
- **app fixture**: Builds Flask app with blueprints and mocked resources
- **client fixture**: Flask test client
- **mock_agent fixture**: Configurable SealineAgent mock
- **sample_file_record fixture**: Factory for creating FileRecord instances with real files on disk

### Mocking Strategy
- **SealineAgent**: Mocked via sys.modules injection (the agent module is replaced with a fake module containing a MagicMock SealineAgent class)
- **pyodbc**: Fake module in sys.modules
- **DB health check**: Patched via `@patch("server.routes.health._check_db_connection")`
- **Config**: MagicMock with all required attributes set
- **No real API calls**: All tests run fully offline

### Compatibility Note
Python 3.9 does not support the `type | None` union syntax used in `server/config.py` line 59. The test infrastructure works around this by injecting fake modules into `sys.modules` before the import chain reaches `server.config` via `server.core.agent -> server.core.sql_executor -> server.config`.

---

## Execution Output

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
collected 117 items
117 passed in 0.83s
============================= 117 passed in 0.83s ==============================
```

All 117 tests passed with zero failures in 0.83 seconds.
