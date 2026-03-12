# QA Logic Review: API Route Modules

**Reviewer:** QA Engineering
**Date:** 2026-03-11
**Scope:** All Flask API route modules, application factory
**Reference:** PRD v1.0 (Sealine Data Chat)

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 6 |
| MODERATE | 11 |
| LOW | 8 |
| SECURITY | 4 |

---

## 1. CRITICAL BUGS

### CRIT-01: Session state not persisted on error in SSE generator (messages.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Lines:** 128-236

**Description:** When an exception occurs inside the `generate()` SSE generator (lines 192-236), the error handler emits an SSE error event and a `message_end` event, but it does **not** persist any partial session state. The `session.messages`, token counters, and other state updates (lines 159-179) are skipped entirely. This means:

- If the agent partially processed a message and the error happened mid-stream (e.g., during a second tool-use loop iteration), the user's original message was already appended to the agent's internal `messages` list, but the session's `messages` field is never updated. On the next request, the session will replay as if the previous message never happened, creating a confusing conversational gap.
- Token counters accumulated before the error are lost, leading to inaccurate usage reporting.

**Suggested Fix:** In the `except` block, attempt to persist whatever partial state the agent has accumulated:

```python
except Exception as exc:
    # ... error event emission ...

    # Attempt partial state persistence
    try:
        if 'agent' in locals():
            session.messages = agent.messages
            session.total_input_tokens = agent.total_input_tokens
            session.total_output_tokens = agent.total_output_tokens
            session.cache_hits = agent.cache_hits
            session.sql_calls = agent.sql_calls
            session.last_active = datetime.now(timezone.utc)
    except Exception:
        pass  # Best-effort
```

---

### CRIT-02: Teams webhook references `agent` variable in `except` scope even when agent creation fails (teams.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/teams.py`
**Lines:** 168-228

**Description:** The agent is instantiated at line 169 inside a `try` block. If the `SealineAgent.__init__()` constructor raises an exception (e.g., invalid model name, docs_text is malformed), execution jumps to the `except` block at line 183. The `except` block sets `response_text` to a user-friendly error string. However, the state persistence code at lines 218-228 then attempts to access `agent.messages`, `agent.total_input_tokens`, etc. The comment at line 226 acknowledges this ("If the agent errored before being created, we may not have an `agent` object"), but the `except Exception: pass` swallows `UnboundLocalError` silently.

While this does not crash the endpoint (the bare `except` catches the `UnboundLocalError`), it creates a **worse problem**: if `agent` **was** successfully created (at line 169) but `agent.send_message_sync()` (line 181) raised the exception, the `except` block at line 183 sets a new `response_text` but does not use the error-specific `error_code` in the HTTP response. The function then falls through to lines 233-243 where it checks the user's **input** `message_text` for report keywords, and **appends** a web UI hint to the **error response text**. So a user who asks "Can you generate a report?" and hits a rate limit error will receive:

```
I'm currently experiencing high demand. Please try again in a moment.

_For reports, charts, and file downloads, visit the web interface at http://localhost:8080_
```

This is misleading -- the report was never generated, but the response implies it might be available at the web UI.

**Suggested Fix:** Move the report-keyword check inside the success path only, and restructure error handling to be explicit about whether the agent ran successfully:

```python
    agent_succeeded = False
    try:
        agent = SealineAgent(...)
        response_text = agent.send_message_sync(message_text)
        agent_succeeded = True
    except Exception as exc:
        # ... error handling ...

    if agent_succeeded:
        # persist state
        session.messages = agent.messages
        ...
        # check for report keywords
        if any(kw in message_text.lower() for kw in report_keywords):
            ...
```

---

### CRIT-03: Teams session re-keying is fragile and can lose sessions (teams.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/teams.py`
**Lines:** 71-95

**Description:** The `_get_or_create_teams_session()` function creates a session via `store.create()` (which generates a random UUID), then deletes it from the store, changes its `session_id` attribute, and manually re-inserts it into the store's internal dictionary. This approach has multiple problems:

1. **Race condition between delete and re-insert (lines 79-88):** Between `store.delete(old_id)` and `store.sessions[session_id] = session`, another thread could call `store.list_sessions()` or `store.cleanup_expired()` and not see the session at all. The session is in limbo -- deleted from the store but not yet re-inserted.

2. **`store.delete()` also cleans up files (store.py line 202):** Calling `store.delete(old_id)` triggers `_cleanup_session_files()`, which will delete any files on disk associated with the session. For a brand-new session this is harmless (no files yet), but the pattern is dangerous if ever reused.

3. **Fallback attribute probing (lines 85-88):** The code tries `store.sessions` then `store._sessions`. If the `SessionStore` class renames its internal dict, this code silently fails and the session is lost forever -- `store.delete(old_id)` already removed it, and the re-insert never happens. No error is raised.

4. **No lock acquisition:** The manual dict insertion at line 86/88 does not hold the store's `_lock`, violating the thread-safety guarantee of the store.

**Suggested Fix:** Add a `put(session_id, session)` method to `SessionStore` that properly acquires the lock and inserts the session. Or better, allow `store.create()` to accept an explicit `session_id` parameter.

---

### CRIT-04: SSE generator runs outside Flask application context (messages.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Lines:** 90-97, 112-236

**Description:** The code correctly captures `cfg`, `docs_text`, and `docs_files` before entering the generator (lines 95-97, with a comment explaining why). However, the generator at line 168 does `from server.sessions.store import FileRecord` **inside** the generator. This import itself is fine (Python caches module imports), but the broader concern is that the generator accesses `session` (captured via closure from line 81) and mutates it (lines 159-179). The `session` object is a reference to the object in the store's `_sessions` dict.

The critical issue: under a production WSGI server with gevent, if the client disconnects mid-stream, the generator may be garbage-collected without completing the state persistence block (lines 157-190). The `message_start` event has been emitted, the agent may have partially run, but `session.messages` is never updated. This leaves the session in an inconsistent state where the agent processed a message but the session history does not reflect it.

**Suggested Fix:** Wrap the generator in a `try/finally` that ensures state persistence:

```python
def generate():
    agent = None
    try:
        # ... existing code ...
    except Exception as exc:
        # ... existing error handling ...
    finally:
        if agent is not None:
            try:
                session.messages = agent.messages
                # ... persist other state ...
            except Exception:
                logger.exception("Failed to persist session state in finally block")
```

---

### CRIT-05: Health check always reports "healthy" even when critical services are down (health.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/health.py`
**Lines:** 43-67

**Description:** The health endpoint always returns `{"status": "healthy"}` with HTTP 200, even when `db_connected` is `False`. Per the PRD (Section 6.6), the health response includes `db_connected` as a field, but there is no guidance on what HTTP status to return when the DB is down. However, load balancers and monitoring tools that use `/api/health` as a liveness/readiness probe will see HTTP 200 and assume the service is fully operational, even though it cannot execute SQL queries -- which is a core capability.

**Suggested Fix:** Consider returning HTTP 200 for liveness (process is alive) but include a `"status": "degraded"` when `db_connected` is `False`. Alternatively, add a separate `/api/health/ready` endpoint that returns 503 when the DB is down.

---

### CRIT-06: Concurrent messages to the same session cause state corruption (messages.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Lines:** 80-82, 143, 159

**Description:** There is no locking or concurrency control on the session during message processing. If two concurrent requests are made to `POST /api/sessions/{session_id}/messages`:

1. Both read `session.messages` at line 143 via `list(session.messages)` (creating independent copies).
2. Both run their respective agents independently.
3. Both write back to `session.messages` at line 159.
4. The second one to finish overwrites the first one's state. All messages from the first request are lost.

The PRD targets 1-5 concurrent users, but multiple browser tabs or rapid double-clicks could trigger this. With gevent workers, these requests run as concurrent green threads in the same process.

**Suggested Fix:** Add a per-session lock or a "busy" flag:

```python
if getattr(session, '_processing', False):
    return _error("Session is busy processing another message", "SESSION_BUSY", 409)
session._processing = True
```

Or use a per-session threading lock to serialize message processing for the same session.

---

## 2. MODERATE ISSUES

### MOD-01: `session.messages` counted incorrectly for user turns with `str` content (sessions.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py`
**Lines:** 90-102

**Description:** The message counting logic checks if `content` is a `str` (counts it) or a `list` (checks if first element is a `tool_result` dict). However, it does not handle the case where `content` is a list but the list is **empty** (`content == []`). If the agent ever produces a user message with an empty list content, `content[0]` at line 99 would not be reached (because `content` is falsy at line 98), so it would silently skip counting it. This is a minor logic gap but could lead to underreported `message_count`.

Additionally, the `Session.to_metadata()` method in `store.py` (line 109) uses `len(self.messages)` (total messages of all roles) for `message_count`, while this route uses a custom user-turn-only count. The two return different values for the same conceptual field, which is confusing for API consumers.

**Suggested Fix:** Use `Session.to_metadata()` in the route handler, or align the counting logic. At minimum, document the difference.

---

### MOD-02: `_find_file_record()` performs O(n*m) scan with no caching (files.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/files.py`
**Lines:** 37-56

**Description:** Every file download request iterates through all sessions and all files within each session to find the matching `file_id`. For each session, it calls `store.get(summary["session_id"])` which acquires and releases the store lock. With many sessions and files, this becomes a performance bottleneck. More importantly, during the iteration, sessions could be deleted by the cleanup thread, causing `KeyError` exceptions (caught at line 50, but the scan continues).

**Suggested Fix:** Add a file-ID-to-session-ID index in the session store for O(1) lookups:

```python
# In SessionStore
self._file_index: dict[str, str] = {}  # file_id -> session_id
```

---

### MOD-03: No request timeout or 15-second proactive response for Teams (teams.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/teams.py`
**Lines:** 168-181

**Description:** The PRD (Section 11.4) requires: "Must respond within 15 seconds or send a 'thinking...' proactive message first." The current implementation calls `agent.send_message_sync(message_text)` synchronously with no timeout. Complex queries involving multiple SQL calls and Claude API roundtrips can easily exceed 15 seconds. Azure Bot Service will retry or timeout the webhook, potentially causing duplicate processing.

**Suggested Fix:** Implement a timeout mechanism. Either:
- Set a 14-second alarm and return a "thinking" response if not done in time, then update via proactive messaging.
- Or at minimum, document the limitation and set a hard timeout.

---

### MOD-04: File deletion race condition between session delete and TTL cleanup (sessions.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py`
**Lines:** 148-161

**Description:** The `delete_session` route manually iterates `session.files` and removes files from disk (lines 149-155), then calls `store.delete(session_id)` (line 159). However, `store.delete()` in `store.py` (line 192-203) **also** calls `_cleanup_session_files()`. This means files are attempted to be deleted twice. While the second attempt is harmless (it checks `os.path.isfile()` first), it is wasteful and introduces a race: if the background cleanup thread fires `cleanup_expired()` between the route's file deletion and `store.delete()` call, the cleanup thread might encounter the session, try to delete files that are already gone, and then delete the session before the route's `store.delete()` runs. The route's `store.delete()` then silently does nothing (KeyError is caught at line 160-161), which is fine, but the route logs "Session deleted" at line 163 even though the cleanup thread actually did the deletion.

**Suggested Fix:** Remove the manual file cleanup from the route (lines 149-155) and let `store.delete()` handle it, since it already does file cleanup internally.

---

### MOD-05: SSE error event missing `recoverable` field in spec (messages.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Lines:** 221-225

**Description:** The error SSE event always emits `"recoverable": False`. Per the PRD (Section 14.2), the `recoverable` flag should indicate whether the agent can continue. Some errors (like a transient SQL timeout) are genuinely recoverable -- the agent could retry with a different query. Hardcoding `False` means the client always treats errors as terminal, which conflicts with the PRD specification.

**Suggested Fix:** Set `recoverable` based on the error type:

```python
recoverable = error_code in ("DB_UNAVAILABLE", "RATE_LIMITED")
```

---

### MOD-06: `_strip_mention()` only handles `<at>` tags, not plain @mentions (teams.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/teams.py`
**Lines:** 45-52

**Description:** The `_strip_mention()` function uses `re.sub(r"<at>.*?</at>", ...)` to remove Bot Framework mention tags. However, in some Teams scenarios (personal chats, older clients), the bot name may appear as a plain `@BotName` text without the `<at>` tags. In those cases, the bot name remains in the message text and gets sent to Claude as part of the user query, which could confuse the agent.

**Suggested Fix:** Add a fallback to strip the bot display name if available from the activity's `entities` list.

---

### MOD-07: No input size validation on message body (messages.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Lines:** 59-73

**Description:** The message endpoint accepts arbitrarily large message strings. A user could POST a multi-megabyte message body, which would be sent to the Anthropic API as-is, consuming a large number of input tokens and potentially hitting API limits. There is no validation on the length of `body.get("message")`.

**Suggested Fix:** Add a maximum message length check:

```python
MAX_MESSAGE_LENGTH = 100_000  # characters
if len(message) > MAX_MESSAGE_LENGTH:
    return _error(
        f"Message exceeds maximum length of {MAX_MESSAGE_LENGTH} characters",
        "INVALID_REQUEST",
        400,
    )
```

---

### MOD-08: `init_health()` global state is not thread-safe (health.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/health.py`
**Lines:** 20-26

**Description:** The `_start_time` module-level global is modified by `init_health()` without any thread safety. If multiple app factory calls happen concurrently (unlikely in production but possible in tests), the global could be overwritten by one factory while another is reading it. The `global _start_time` pattern is also fragile -- if the module is reloaded (e.g., by a test runner), the state is reset.

**Suggested Fix:** Store `_start_time` on the Flask `app` object instead of as a module global, and access it via `current_app` in the health route.

---

### MOD-09: Database health check creates a full connection on every call (health.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/health.py`
**Lines:** 29-40

**Description:** Every `GET /api/health` request opens a new pyodbc connection, runs zero queries, and closes it. This is wasteful and adds ~0.5-2 seconds of latency to health checks. If monitoring tools poll this endpoint every 10-30 seconds, it creates unnecessary load on the SQL Server.

**Suggested Fix:** Cache the health check result for a short duration (e.g., 30 seconds), or use a connection pool with a lightweight ping.

---

### MOD-10: Catch-all static route can shadow API routes on path collision (app.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/app.py`
**Lines:** 138-153

**Description:** The catch-all route `/<path:path>` at line 138 is registered on the app directly (no URL prefix). The API blueprints use `/api` prefix. If a static file happens to exist at `static/api/health` (e.g., from a bad React build), the catch-all could intercept the request before the blueprint. Flask's routing resolves this by priority (more specific wins), and blueprints registered first should take priority, but the interaction is fragile and depends on registration order.

Additionally, the catch-all returns `("", 404)` when no index.html exists (line 153). Returning an empty string with 404 gives no useful feedback to the user or developer.

**Suggested Fix:** Add a check to exclude `/api` paths from the catch-all:

```python
@app.route("/<path:path>")
def serve_static_or_fallback(path: str):
    if path.startswith("api/"):
        return jsonify({"error": "Not found", "code": "NOT_FOUND", "status": 404}), 404
    # ... rest of the function
```

---

### MOD-11: `store.get()` returns a mutable reference without copy protection (store.py / messages.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/sessions/store.py`
**Lines:** 180-189

**Description:** `SessionStore.get()` returns a direct reference to the `Session` object stored in the internal `_sessions` dict. Multiple concurrent requests can hold references to the same session object and mutate it simultaneously (e.g., appending to `session.messages` from two threads). While `messages.py` creates a copy via `list(session.messages)` at line 143 for the agent, the write-back at line 159 (`session.messages = agent.messages`) is not protected by any lock.

**Suggested Fix:** This is related to CRIT-06. Adding a per-session lock would address both issues.

---

## 3. LOW ISSUES

### LOW-01: Duplicated `_error()` helper function across all route modules

**Files:**
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py` (line 25)
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py` (line 20)
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/files.py` (line 32)
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/teams.py` (line 40)

**Description:** The `_error()` helper function is copy-pasted identically in four route modules. Any change to the error response format (e.g., adding a `timestamp` field) must be applied in four places. This is a maintenance burden and a source of potential inconsistency.

**Suggested Fix:** Extract to a shared utility module:

```python
# server/routes/utils.py
def error_response(message, code, status):
    return jsonify({"error": message, "code": code, "status": status}), status
```

---

### LOW-02: Inconsistent `usage` field naming between routes and PRD

**Files:**
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py` (lines 118-123)
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py` (lines 183-189)

**Description:** The GET session endpoint returns `"cache_hits"` in the usage dict (line 122), while the SSE `message_end` event returns `"cache_read_tokens"` (line 187). The PRD Section 6.2 specifies `cache_read_tokens` for the SSE event, and Section 6.3 specifies `cache_hits` for the session info. However, these represent different metrics: `cache_hits` is a count of cache hit events, while `cache_read_tokens` (in the SSE) actually reports the `cache_hits` field from the session object. This is confusing and likely a bug -- the SSE event reports `session.cache_hits` (a count) under the name `cache_read_tokens` (suggesting a token count).

**Suggested Fix:** Align the naming with the PRD. Either rename consistently, or ensure the SSE `message_end` event reports actual cache read token counts rather than the `cache_hits` counter.

---

### LOW-03: `db_enabled` check via `import pyodbc` is repeated in three places

**Files:**
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py` (lines 100-104)
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py` (lines 50-54)
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/teams.py` (lines 157-161)

**Description:** The `try: import pyodbc; db_enabled = True; except ImportError: db_enabled = False` pattern is copy-pasted in three files. This is a trivial check that should be done once at app startup and stored as a config/app attribute.

**Suggested Fix:** Set `app.db_enabled` in `create_app()` and access via `current_app.db_enabled` in routes.

---

### LOW-04: Missing `Content-Type: application/json` validation on POST endpoints

**Files:**
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py` (line 59)
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/teams.py` (line 111)

**Description:** The `request.get_json(silent=True)` call returns `None` both when the body is not JSON and when the `Content-Type` header is not `application/json`. While `silent=True` prevents an exception, it also means that a request with valid JSON body but wrong Content-Type (e.g., `text/plain`) is silently rejected with a generic "Request body must be valid JSON" error. This can be confusing for API consumers.

**Suggested Fix:** Check `request.content_type` explicitly and return a more specific error if the Content-Type is wrong but the body might be valid JSON.

---

### LOW-05: `create_session` returns 201 without `Location` header

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py`
**Lines:** 58-63

**Description:** Per HTTP standards, a `201 Created` response should include a `Location` header pointing to the newly created resource. The response returns the `session_id` in the body but does not include `Location: /api/sessions/{session_id}`.

**Suggested Fix:** Add the `Location` header:

```python
response = jsonify({...})
response.status_code = 201
response.headers["Location"] = f"/api/sessions/{session.session_id}"
return response
```

---

### LOW-06: SSE message_id uses truncated UUID (12 hex chars)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Line:** 107

**Description:** The message ID is generated as `f"msg_{uuid.uuid4().hex[:12]}"`, which is 12 hex characters (48 bits of randomness). With a birthday paradox, there is a ~1% collision probability after ~16 million messages. While V1 targets low concurrency, using a full UUID would be more robust and cost-negligible.

**Suggested Fix:** Use the full UUID: `f"msg_{uuid.uuid4().hex}"`.

---

### LOW-07: Anthropic client created in app.py but never passed to routes

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/app.py`
**Lines:** 89-91, 98

**Description:** An `anthropic.Anthropic` client is created with `verify=False` and stored as `app.config["ANTHROPIC_CLIENT"]`, but it is never accessed by any route. The `SealineAgent` in `messages.py` and `teams.py` presumably creates its own client internally. This means the SSL bypass client configured here is wasted, and each agent request may create a new HTTP client.

**Suggested Fix:** Either pass the shared `anthropic_client` to the agent constructor, or remove the unused client creation from `app.py` to avoid confusion.

---

### LOW-08: File download serves HTML inline without sanitization (files.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/files.py`
**Lines:** 96, 106-111

**Description:** HTML files (`.html`) are served inline (`as_attachment = False` when ext is `.html`). If a generated HTML file contains malicious JavaScript (e.g., injected via a crafted SQL result that the agent embedded in a Plotly chart), serving it inline means the script executes in the same origin as the API. This is a stored XSS vector.

**Suggested Fix:** Either serve HTML files as attachments, or serve them with `Content-Security-Policy: sandbox` to prevent script execution. Alternatively, serve them from a different origin/subdomain.

---

## 4. SECURITY ISSUES

### SEC-01: No path traversal protection on file serving (files.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/files.py`
**Lines:** 106-111

**Description:** The `send_file(fr.file_path, ...)` call uses `fr.file_path` directly from the `FileRecord` stored in the session. While file paths are generated server-side by the agent, a bug in the agent's file generation code (or a corrupted session) could set `file_path` to an arbitrary path like `/etc/passwd`. Flask's `send_file()` will happily serve any file the process has read access to.

**Suggested Fix:** Validate that the resolved path is within the expected `file_store_path` directory:

```python
real_path = os.path.realpath(fr.file_path)
allowed_dir = os.path.realpath(current_app.config["FILE_STORE_PATH"])
if not real_path.startswith(allowed_dir + os.sep):
    return _error("File path is outside allowed directory", "FILE_NOT_FOUND", 403)
```

---

### SEC-02: No JWT token validation on Teams webhook (teams.py)

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/teams.py`
**Lines:** 98-259

**Description:** The PRD (Section 6.7) specifies: "Validates the incoming JWT token from Azure Bot Service." The current implementation does **no authentication whatsoever** on the Teams webhook. Any client that can reach `POST /api/teams/messages` can send a crafted Bot Framework Activity and get the agent to execute SQL queries and return results. This is a significant security gap -- the Teams endpoint is effectively an unauthenticated data query API.

**Suggested Fix:** Implement Bot Framework JWT token validation using the `microsoft-agents` SDK or manual JWT verification against the Bot Framework public keys.

---

### SEC-03: Exception details leaked in error responses (sessions.py, messages.py)

**Files:**
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py` (line 44)
- `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py` (line 204)

**Description:** Session creation errors include the raw exception message: `f"Failed to create session: {exc}"` (sessions.py line 44). SSE error events include `str(exc)` as the error message (messages.py line 204). These can leak internal implementation details, stack traces, or database error messages to the client.

**Suggested Fix:** Return generic error messages to the client and log the full exception server-side (which is already done via `logger.exception()`). Replace `str(exc)` with a generic message in the SSE error event for unclassified errors.

---

### SEC-04: Database credentials hardcoded as defaults in config.py

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/config.py`
**Lines:** 28-29

**Description:** The database username (`sean`) and password (`4peiling`) are hardcoded as default values in the `Config` class. While they are intended as development defaults and overridden by environment variables in production, the credentials are in source code and would be exposed if the repository were made public or shared with unauthorized parties.

**Suggested Fix:** Remove default values for sensitive credentials and require them to be set via environment variables. Fail fast at startup with a clear error if they are not set.

---

## 5. SSE STREAMING ANALYSIS (messages.py)

### SSE-01: No SSE retry/keepalive mechanism

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Lines:** 241-249

**Description:** The SSE response does not include an `id:` field or a `retry:` field in the event stream. The SSE specification allows clients to set `Last-Event-ID` headers to resume broken connections. Without event IDs, if the connection drops mid-stream, the client has no way to resume -- it must re-send the message, which would create a duplicate agent invocation and duplicate token charges.

Additionally, there are no SSE comment keepalive frames (`: keepalive\n\n`) emitted during long pauses (e.g., while waiting for a SQL query). Proxies or load balancers with idle connection timeouts could terminate the connection.

**Suggested Fix:** Add a `retry:` field to the stream header and consider periodic keepalive comments:

```python
def generate():
    yield "retry: 5000\n\n"  # Client should retry after 5 seconds
    yield _sse_line("message_start", {...})
    # ... rest of generator ...
```

---

### SSE-02: SSE event format uses named events which require EventSource workaround

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Lines:** 30-32

**Description:** The `_sse_line()` function emits named events (e.g., `event: text_delta`). The PRD (Section 7.4) correctly notes that the frontend uses `fetch` with `ReadableStream` rather than native `EventSource` (since `EventSource` only supports GET). However, the SSE format specification requires named events to be handled via `addEventListener()` rather than `onmessage` with native `EventSource`. This is consistent with the fetch-based approach, but it is worth noting that switching to a standard `EventSource` client in the future would require registering listeners for each event type.

This is informational, not a bug.

---

### SSE-03: Generator does not handle `GeneratorExit` for clean shutdown

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
**Lines:** 112-236

**Description:** When a client disconnects mid-stream, the WSGI server calls `.close()` on the generator, which raises `GeneratorExit` inside the generator. The current code does not catch `GeneratorExit`, so the generator is simply terminated at whatever `yield` point it was at. If this happens between the agent completing and the state persistence block (lines 157-190), the session state is not saved.

**Suggested Fix:** Handle `GeneratorExit` explicitly:

```python
def generate():
    agent = None
    try:
        # ... existing code ...
    except GeneratorExit:
        logger.info("Client disconnected for session %s", session_id)
        if agent is not None:
            session.messages = agent.messages
            # ... persist partial state ...
        return
    except Exception as exc:
        # ... existing error handling ...
```

---

## 6. CROSS-ROUTE CONSISTENCY

### CROSS-01: PRD specifies HTTP 503 for rate limiting; code returns 200 (SSE) or no status differentiation

**PRD Ref:** Section 14.1

**Description:** The PRD error table specifies HTTP 503 for `RATE_LIMITED` and HTTP 502 for `CLAUDE_API_ERROR`. However, the messages route always returns HTTP 200 (because the SSE stream has already started), and the error codes are only emitted as SSE events. The Teams route returns HTTP 200 with the error embedded in the Bot Framework Activity text. No route ever returns HTTP 502 or 503. This is partially by design (SSE streams cannot change HTTP status mid-stream), but the PRD specification is misleading.

**Suggested Fix:** Update the PRD to clarify that within SSE streams, errors are delivered as SSE events with 200 status. For non-streaming errors (e.g., if the Claude API is down before the stream starts), consider checking API connectivity early and returning 503 before starting the stream.

---

### CROSS-02: No 405 Method Not Allowed handling for wrong HTTP methods

**All Route Files**

**Description:** None of the routes define handlers for unsupported HTTP methods. For example, `GET /api/sessions` (without an ID) will return Flask's default 405 response, which is plain HTML, not the JSON format specified in the PRD. Similarly, `DELETE /api/files/{file_id}` returns a default 405.

**Suggested Fix:** Add a custom 405 error handler to the Flask app:

```python
@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed", "code": "METHOD_NOT_ALLOWED", "status": 405}), 405
```

---

### CROSS-03: No global 404 handler for undefined API routes

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/app.py`

**Description:** Requests to undefined routes under `/api/` (e.g., `GET /api/nonexistent`) fall through to the catch-all `/<path:path>` handler (line 138), which attempts to serve a static file. If no static file exists, it returns the React `index.html` (a full HTML page) with HTTP 200 for what should be a 404 JSON error. API clients expecting JSON will receive unexpected HTML.

**Suggested Fix:** Add a catch-all 404 handler specifically for `/api` routes:

```python
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found", "code": "NOT_FOUND", "status": 404}), 404
    # Serve React fallback for non-API routes
    return send_from_directory(static_dir, "index.html")
```

---

## 7. APP FACTORY (app.py)

### APP-01: SSL verification disabled globally for Anthropic client

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/app.py`
**Lines:** 89-91

**Description:** The `httpx.Client(verify=False)` disables SSL certificate verification for all requests made through this client. While necessary for the corporate proxy (PRD Section 13.2), the `verify=False` suppresses Python warnings but creates a MITM vulnerability. The PRD acknowledges this is "isolated to the Anthropic httpx client only," which is correct in the current implementation.

This is an accepted risk per the PRD and is informational only.

---

### APP-02: Duplicate storage of shared resources

**File:** `/Users/peter_parker/Desktop/Sealine-Database/server/app.py`
**Lines:** 95-107

**Description:** Shared resources are stored both in `app.config` (standard Flask dict, lines 95-100) and as direct attributes on the `app` object (lines 103-107). Routes only use the direct attributes (`current_app.session_store`, etc.), making the `app.config` entries dead code. This duplication creates a maintenance hazard -- if someone updates `app.config["SESSION_STORE"]` thinking it is the authoritative source, the routes will not see the change.

**Suggested Fix:** Remove either the `app.config` entries or the direct attributes. Standardize on one approach.

---

## End of Report
