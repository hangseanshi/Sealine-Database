# Fixes Applied — Post-QA Remediation

**Date:** 2026-03-11
**Status:** All critical and security fixes applied. All 354 tests passing.

---

## Server-Side Fixes (14 total)

### 🔴 CRITICAL Fixes

| # | Fix | File | What Changed |
|---|-----|------|-------------|
| 1 | SQL injection via EXEC/EXECUTE | `sql_executor.py` | Removed `EXEC`/`EXECUTE` from `_ALLOWED_FIRST_WORDS`. Now only `SELECT` and `WITH` are allowed. |
| 2 | SQL injection via multi-statement | `sql_executor.py` | Added `_DANGEROUS_KEYWORDS` scan that checks the full query body for `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, `REVOKE`, `EXEC`, `EXECUTE`, `XP_`, `SP_CONFIGURE`, `SHUTDOWN`, `DBCC`. |
| 3 | Database connection leak | `sql_executor.py` | Added `finally` block that always closes `conn`. Connection is now `conn = None` before try block. |
| 4 | Query execution timeout | `sql_executor.py` | Added `conn.timeout = 30` for query execution timeout (not just connection timeout). |
| 5 | Infinite tool-use loop | `agent.py` | Added `MAX_TOOL_LOOPS = 15` constant and loop counter. Emits error event and breaks when exceeded. |
| 6 | Matplotlib figure leak (pie charts) | `file_generator.py` | Changed `ax.remove()` to `plt.close(fig)` before creating new figure for pie charts. |
| 7 | Session cleanup race condition | `store.py` | Expired sessions are now popped atomically under the lock (instead of collecting IDs under lock, then deleting outside lock). File cleanup happens after sessions are removed from the dict. |
| 8 | BadRequestError retry | `agent.py` | Retry now only happens if error message contains "thinking". Other BadRequestErrors are re-raised to the outer handler. |

### 🔒 SECURITY Fixes

| # | Fix | File | What Changed |
|---|-----|------|-------------|
| 9 | Hardcoded DB credentials | `config.py` | Removed `"sean"` and `"4peiling"` defaults. `DB_SERVER`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` all default to `""`. |
| 10 | Error message sanitization | `agent.py` | All Anthropic error handlers now log the raw exception but return generic user-facing messages (no API keys, no internal details). Rate limit and connection errors marked as `recoverable: true`. |

### 🟡 MODERATE Fixes

| # | Fix | File | What Changed |
|---|-----|------|-------------|
| 11 | Usage counter accumulation | `messages.py` | Changed `session.total_input_tokens = agent.total_input_tokens` to `session.total_input_tokens += agent.total_input_tokens` (and all other counters). |
| 12 | `generated_files` reset | `agent.py` | Added `self.generated_files = []` at the start of `send_message()` to prevent accumulation across multiple calls. |
| 13 | Error dict guard | `messages.py` | Added `if "error" in fdict or "file_id" not in fdict: continue` to skip error returns from file generators. |
| 14 | Double file deletion | `sessions.py` | Removed manual file deletion loop from DELETE route; `store.delete()` already handles file cleanup. |

### Code Quality

| # | Fix | File | What Changed |
|---|-----|------|-------------|
| 15 | Dead code `or True` | `agent.py` | Changed `if _FILE_TOOLS_AVAILABLE or True:` to `if True:` with proper comment. |
| 16 | Empty query guard | `sql_executor.py` | Added early return for empty/whitespace-only queries. |

---

## Frontend Fixes (4 total)

| # | Fix | File | What Changed |
|---|-----|------|-------------|
| 1 | Abort SSE on session switch | `useSSE.js`, `App.jsx` | Added `abort()` function to `useSSE`. Called in `handleSelectSession` before switching. Prevents cross-session data contamination. |
| 2 | Unmount cleanup | `useSSE.js` | Added `useEffect` cleanup that aborts active stream when component unmounts. Prevents memory leaks and stale state updates. |
| 3 | Stable React keys | `App.jsx`, `ChatArea.jsx` | Added incrementing `id` counter (`getNextId()`) to all message objects. `ChatArea` uses `key={msg.id ?? idx}` instead of `key={idx}`. |
| 4 | Streaming target in `onTextDelta` | `App.jsx` | Changed `messages[i].type === 'agent'` to `messages[i].type === 'agent' && messages[i].isStreaming` to prevent appending text to completed messages. |

---

## Test Updates

- Updated 14 tests to match new behavior:
  - `test_config.py`: DB credential defaults now `""` instead of hardcoded values
  - `test_sql_executor.py`: EXEC/EXECUTE queries now expected to be blocked; error messages sanitized; allowed words assertion updated
- **Final result: 354 tests, all passing**

---

## Remaining Issues (Not Fixed — Deferred to V1+)

These were identified by the QA review but deferred as lower priority:

- No rate limiting or input size validation (V2)
- No authentication on file download endpoint (V2)
- No JWT validation on Teams webhook (V2)
- SSL verification disabled (`verify=False`) — intentional for corporate SSL bypass
- Multi-worker session store incompatibility (`WORKERS=2`) — set to 1 for V1
- No per-session processing lock — acceptable for 1-5 users
- Various frontend UX improvements (auto-scroll awareness, markdown rendering performance, accessibility)
