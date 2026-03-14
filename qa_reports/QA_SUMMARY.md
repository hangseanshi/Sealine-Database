# QA Summary Report — Sealine Data Chat Application

**Date:** 2026-03-11
**QA Team:** 5 parallel QA agents (2 logic reviewers, 2 unit test writers, 1 frontend reviewer)
**Scope:** Full-stack review: server core, API routes, session management, frontend React app

---

## 1. Unit Test Results

### Overall: ✅ 337 tests — ALL PASSING

| Test File | Tests | Status |
|-----------|-------|--------|
| `test_file_generator.py` | 76 | ✅ All pass |
| `test_session_store.py` | 53 | ✅ All pass |
| `test_config.py` | 44 | ✅ All pass |
| `test_sql_executor.py` | 43 | ✅ All pass |
| `test_routes_teams.py` | 22 | ✅ All pass |
| `test_routes_sessions.py` | 21 | ✅ All pass |
| `test_context_loader.py` | 21 | ✅ All pass |
| `test_routes_messages.py` | 20 | ✅ All pass |
| `test_routes_health.py` | 16 | ✅ All pass |
| `test_routes_files.py` | 14 | ✅ All pass |
| `test_app.py` | 7 | ✅ All pass |
| **TOTAL** | **337** | **✅ 337 passed** |

### Coverage Areas Tested:
- **Config:** Default values, env var overrides, connection string construction, singleton pattern, type coercion
- **Context Loader:** Single/multiple .md file loading, empty/missing directories, non-.md file filtering, encoding handling
- **SQL Executor:** Allowed queries (SELECT/WITH), blocked queries (DROP/DELETE/INSERT/UPDATE/ALTER/TRUNCATE/CREATE/GRANT), timeout, 500-row truncation, error handling, connection string building
- **File Generator:** Static plots (bar/line/pie/scatter/heatmap), interactive Plotly plots, PDF generation with fallback, Excel generation with headers, file cleanup, input validation
- **Session Store:** CRUD operations, TTL expiry, thread safety (concurrent creates/deletes/reads), FileRecord handling, metadata generation
- **API Routes:** All 5 blueprints tested (sessions, messages, files, health, teams) with full HTTP method/status code coverage
- **App Factory:** Blueprint registration, shared resource binding, static file serving

---

## 2. Logic Review Findings Summary

### Consolidated Issue Counts

| Category | Server Core | API Routes | Frontend | **Total** |
|----------|:-----------:|:----------:|:--------:|:---------:|
| 🔴 CRITICAL | 7 | 6 | 5 | **18** |
| 🟡 MODERATE | 14 | 11 | 12 | **37** |
| 🟢 LOW | 10 | 8 | 11 | **29** |
| 🔒 SECURITY | 7 | 4 | — | **11** |
| **Total** | **38** | **29** | **28** | **95** |

---

## 3. Top Priority Issues (Must Fix Before V1)

### 🔴 CRITICAL — Immediate Action Required

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 1 | **SQL Injection via EXEC/EXECUTE** | `sql_executor.py:32` | Allows arbitrary stored procedure execution, bypassing read-only safety |
| 2 | **SQL injection via multi-statement queries** | `sql_executor.py:80-86` | `SELECT 1; DROP TABLE x` passes first-word check |
| 3 | **Database connection leak on errors** | `sql_executor.py:90-150` | Connection not closed in except block → pool exhaustion |
| 4 | **Infinite tool-use loop** | `agent.py:519-614` | `while True` with no iteration cap → unbounded API costs |
| 5 | **Unbounded conversation history** | `agent.py:249,504,579` | Messages grow without limit → context window exceeded |
| 6 | **Matplotlib figure leak (pie charts)** | `file_generator.py:279-290` | Original figure never closed → memory exhaustion |
| 7 | **Session cleanup race condition** | `store.py:211-236` | Can delete active session between lock release and delete call |
| 8 | **Concurrent messages corrupt session** | `messages.py:80-159` | Two requests to same session → second overwrites first |
| 9 | **SSE stream not aborted on session switch** (Frontend) | `useSSE.js, App.jsx` | Old stream events injected into new session → cross-session data contamination |
| 10 | **React keys use array index** (Frontend) | `ChatArea.jsx:63-127` | State applied to wrong SQL/thinking blocks after re-ordering |

### 🔒 SECURITY — Immediate Action Required

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 1 | **Hardcoded DB credentials** | `config.py:27-29` | Username `sean` / password `4peiling` in source code |
| 2 | **SSL verification disabled** | `app.py:90, agent.py:236` | MITM vulnerability on API communication |
| 3 | **No JWT validation on Teams webhook** | `teams.py:98-259` | Anyone can query the database via unauthenticated endpoint |
| 4 | **No rate limiting or input size validation** | Multiple files | Unlimited API cost exposure, DoS vulnerability |
| 5 | **File download has no authentication** | `files.py:59-111` | Any user can download any other user's reports |

---

## 4. Moderate Issues (Fix Before or Shortly After V1)

### Server-Side

| Issue | Location | Description |
|-------|----------|-------------|
| Usage counters overwrite instead of accumulate | `messages.py:159-163` | Session tokens only reflect last message, not cumulative total |
| BadRequestError retry is identical to first attempt | `agent.py:552-563` | Retry doesn't actually change anything |
| `generated_files` list never reset between messages | `agent.py:258` | Files from previous messages re-appended |
| No query execution timeout | `sql_executor.py:91` | `timeout=30` only for connection, not query execution |
| No file size limit on loaded context | `context_loader.py:33-38` | Huge .md files could exceed API context window |
| Error dict appended to generated_files | `file_generator.py:229-231` | Error returns `{"error": ...}` which gets appended as if it's a file |
| Path traversal risk in filename param | `file_generator.py:503,694` | Insufficient validation on user-supplied filenames |
| Double file deletion (route + store) | `sessions.py:148-155` | Route and store both delete files |
| Teams session re-keying is fragile | `teams.py:71-95` | Race condition, no lock, fragile attribute probing |
| Teams 15-second response requirement not met | `teams.py:168-181` | No timeout mechanism per PRD requirement |
| Session state lost on SSE generator exit | `messages.py:128-236` | Client disconnect → no state persistence |

### Frontend

| Issue | Location | Description |
|-------|----------|-------------|
| Race condition on new chat → send message | `App.jsx:234-301` | Stale closure on `activeSessionId` |
| `onTextDelta` updates wrong message | `App.jsx:50-71` | Finds last agent message, not last *streaming* agent message |
| No abort cleanup on component unmount | `useSSE.js` | Memory leak + state update on unmounted component |
| `isStreaming` is global, not per-session | `useSSE.js:22` | Input bar disabled on unrelated sessions |
| Auto-scroll prevents reading during streaming | `ChatArea.jsx:21-25` | Forces scroll to bottom on every delta |
| O(n²) markdown re-rendering during streaming | `MessageBubble.jsx` | ReactMarkdown re-parses entire text on every delta |
| `onToolResult` matched by position, not ID | `App.jsx:114-137` | Wrong query result displayed if multiple tools run concurrently |
| Sessions not loaded from server on refresh | `App.jsx:20-21` | All sessions lost on page refresh despite server persistence |

---

## 5. Low Priority Issues (Nice to Have)

- Duplicated `_error()` helper across 4 route modules
- `db_enabled` import check repeated in 3 places
- No `Location` header on 201 Created response
- Truncated UUID for message IDs (12 hex chars instead of full)
- Unused Anthropic client in app.py
- No global 404/405 JSON handler for API routes
- Various accessibility issues (keyboard navigation, ARIA labels)
- No confirmation dialog before session delete
- Missing image load error handling in InlinePlot
- `getSessionInfo` function is dead code
- Background cleanup thread cannot be stopped gracefully

---

## 6. Architectural Observations

1. **Multi-worker session store incompatibility:** `WORKERS=2` in gunicorn means 2 processes with separate in-memory session stores. Sessions created in Worker 1 are invisible to Worker 2. Must set `WORKERS=1` or switch to shared store (Redis).

2. **Duplicate Anthropic client:** `app.py` creates a shared client that is never used. Each `SealineAgent` creates its own. Wastes resources and creates confusion.

3. **File store path mismatch risk:** `file_generator.py` defaults to `./tmp/files` (relative), while `app.py` constructs an absolute path. If a code path calls a generator without passing `file_store_path`, files could be split across two directories.

---

## 7. Detailed Reports

Full detailed findings with line numbers, code snippets, and suggested fixes are available in:

- [`qa_reports/logic_review_core.md`](./logic_review_core.md) — Server core modules (38 findings)
- [`qa_reports/logic_review_routes.md`](./logic_review_routes.md) — API routes (29 findings)
- [`qa_reports/logic_review_frontend.md`](./logic_review_frontend.md) — React frontend (28 findings)

---

## 8. Recommended Remediation Priority

### Phase 1: Before V1 Launch (Critical + Security)
1. Remove EXEC/EXECUTE from SQL allowlist
2. Fix database connection leak (use `finally` block)
3. Add tool-use loop iteration cap (e.g., 15)
4. Fix matplotlib figure leak on pie chart path
5. Remove hardcoded DB credentials from config defaults
6. Add per-session processing lock to prevent concurrent corruption
7. Fix session cleanup race condition
8. Frontend: Abort SSE stream on session switch
9. Frontend: Use stable message IDs as React keys
10. Frontend: Target streaming messages in `onTextDelta`

### Phase 2: Shortly After V1 Launch
11. Fix usage counter accumulation (overwrite → increment)
12. Add query execution timeout
13. Add message length validation
14. Implement Teams 15-second timeout
15. Frontend: Add unmount cleanup for SSE hook
16. Frontend: Fix auto-scroll to be user-aware

### Phase 3: Ongoing Improvements
17. Add file-level authentication
18. Add rate limiting
19. Implement proper 404/405 JSON handlers
20. Address accessibility issues
21. Performance optimization (markdown rendering, sidebar re-renders)
