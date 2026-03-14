# Logic Review Report -- Server Core Modules

**Reviewer:** QA Engineering
**Date:** 2026-03-11
**Scope:** `server/core/agent.py`, `server/core/sql_executor.py`, `server/core/context_loader.py`, `server/core/file_generator.py`, `server/config.py`, `server/sessions/store.py`
**Cross-referenced:** `server/routes/messages.py`, `server/routes/sessions.py`, `server/routes/files.py`, `server/app.py`

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 7 |
| SECURITY | 7 |
| MODERATE | 14 |
| LOW | 10 |

---

## 1. `server/config.py`

### SECURITY-001: Hardcoded Database Credentials in Source Code

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/config.py`
- **Lines:** 27-29
- **Severity:** SECURITY / CRITICAL
- **Description:** The database credentials are hardcoded as default values in the configuration class:
  ```python
  self.DB_USER: str = os.environ.get("DB_USER", "sean")
  self.DB_PASSWORD: str = os.environ.get("DB_PASSWORD", "4peiling")
  ```
  These defaults mean the username `sean` and password `4peiling` are committed to version control in plaintext. Even if environment variables override them in production, the credentials are exposed to anyone with repository access.
- **Suggested Fix:** Remove the default values for sensitive fields. Raise a configuration error at startup if `DB_USER` or `DB_PASSWORD` are not set:
  ```python
  self.DB_USER = os.environ.get("DB_USER")
  self.DB_PASSWORD = os.environ.get("DB_PASSWORD")
  if not self.DB_USER or not self.DB_PASSWORD:
      raise EnvironmentError("DB_USER and DB_PASSWORD environment variables are required")
  ```

### SECURITY-002: SSL Verification Disabled on Anthropic API Client

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/config.py` (used in `server/core/agent.py` line 236 and `server/app.py` line 89-91)
- **Lines:** agent.py:236, app.py:90
- **Severity:** SECURITY
- **Description:** The Anthropic client is created with `httpx.Client(verify=False)`, which disables TLS certificate verification. This makes all API communication vulnerable to man-in-the-middle attacks. An attacker on the network could intercept API keys and all data sent to/from the Claude API.
- **Suggested Fix:** Remove `verify=False` or make it configurable via an environment variable (defaulting to `True`):
  ```python
  ssl_verify = os.environ.get("SSL_VERIFY", "true").lower() != "false"
  self.client = anthropic.Anthropic(
      http_client=httpx.Client(verify=ssl_verify),
  )
  ```

### MODERATE-001: Config Singleton is Not Thread-Safe

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/config.py`
- **Lines:** 59-67
- **Severity:** MODERATE
- **Description:** The `get_config()` function uses a module-level `_config` variable without any locking. If two threads call `get_config()` concurrently during initialization (before `_config` is set), two `Config` instances could be created. This is generally benign since both would have the same values, but it violates the singleton contract.
- **Suggested Fix:** Use `threading.Lock()` to guard the singleton creation, or initialize the config at module import time:
  ```python
  _config = Config()  # Initialize at import time
  def get_config() -> Config:
      return _config
  ```

### LOW-001: No Validation on Numeric Environment Variables

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/config.py`
- **Lines:** 16, 18, 23, 32-33
- **Severity:** LOW
- **Description:** Numeric config values are cast with `int()` directly. If an environment variable contains a non-numeric value (e.g., `PORT=abc`), the application crashes with an unhandled `ValueError` at startup with no user-friendly error message.
- **Suggested Fix:** Wrap in try/except with a descriptive error:
  ```python
  try:
      self.PORT = int(os.environ.get("PORT", "8080"))
  except ValueError:
      raise ValueError("PORT environment variable must be an integer")
  ```

---

## 2. `server/core/sql_executor.py`

### SECURITY-003: SQL Injection via Keyword Bypass -- EXEC/EXECUTE Allowed

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/sql_executor.py`
- **Lines:** 32, 80-86
- **Severity:** SECURITY / CRITICAL
- **Description:** The allowed-first-words list includes `EXEC` and `EXECUTE`:
  ```python
  _ALLOWED_FIRST_WORDS = frozenset({"SELECT", "WITH", "EXEC", "EXECUTE"})
  ```
  This allows executing arbitrary stored procedures, which may perform writes, deletes, or administrative operations. An LLM-generated query like `EXEC sp_MSforeachtable 'DROP TABLE ?'` or `EXEC xp_cmdshell 'rm -rf /'` would pass the safety check. The `EXEC` keyword completely undermines the "read-only" safety claim stated in the module docstring.
- **Suggested Fix:** Remove `EXEC` and `EXECUTE` from the allowed keywords. If specific stored procedures must be callable, create an explicit allowlist of procedure names:
  ```python
  _ALLOWED_FIRST_WORDS = frozenset({"SELECT", "WITH"})
  ```

### SECURITY-004: SQL Injection via Subquery Writes

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/sql_executor.py`
- **Lines:** 80-86
- **Severity:** SECURITY
- **Description:** The safety check only validates the **first word** of the query. A malicious query could bypass this with:
  ```sql
  SELECT 1; DROP TABLE users; --
  ```
  or:
  ```sql
  WITH cte AS (SELECT 1) INSERT INTO audit_log SELECT * FROM cte
  ```
  The first-word check does not prevent multi-statement attacks or subquery writes. The `pyodbc.execute()` method can execute multiple statements separated by semicolons.
- **Suggested Fix:** In addition to first-word validation, scan the full query for dangerous keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, `REVOKE`, `xp_`, `sp_`). Better yet, use a read-only database connection or a database user with only SELECT permissions:
  ```python
  # Use read-only intent in connection string:
  conn_str += "ApplicationIntent=ReadOnly;"
  ```

### CRITICAL-001: Database Connection Leak on Query Errors

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/sql_executor.py`
- **Lines:** 90-150
- **Severity:** CRITICAL
- **Description:** The connection is opened at line 91 but the `except` block at line 145 does not close it. If `cursor.execute(q)` raises an exception, or `cursor.fetchmany()` fails, the connection is leaked. Over time, leaked connections will exhaust the database connection pool and bring down the application.
  ```python
  try:
      conn = pyodbc.connect(conn_str, timeout=30)
      cursor = conn.cursor()
      cursor.execute(q)  # If this throws, conn is never closed
      ...
      conn.close()       # Only reached on success
  except Exception as e:
      # conn.close() is NOT called here
      return SqlResult(text=f"SQL ERROR: {e}", error=True)
  ```
- **Suggested Fix:** Use a `finally` block or context manager:
  ```python
  conn = None
  try:
      conn = pyodbc.connect(conn_str, timeout=30)
      cursor = conn.cursor()
      cursor.execute(q)
      ...
  except Exception as e:
      logger.exception("SQL execution error")
      return SqlResult(text=f"SQL ERROR: {e}", error=True)
  finally:
      if conn:
          try:
              conn.close()
          except Exception:
              pass
  ```

### MODERATE-002: No Query Timeout at Cursor Level

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/sql_executor.py`
- **Lines:** 91-93
- **Severity:** MODERATE
- **Description:** The `timeout=30` parameter on `pyodbc.connect()` applies only to the **connection** timeout (how long to wait to establish a connection), not to query execution time. A long-running query (e.g., a full table scan or cartesian join) could run indefinitely, tying up the connection and the server thread.
- **Suggested Fix:** Set a query execution timeout on the connection:
  ```python
  conn = pyodbc.connect(conn_str, timeout=30)
  conn.timeout = 30  # Query execution timeout in seconds
  ```

### MODERATE-003: Potential IndexError on Empty Query String

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/sql_executor.py`
- **Lines:** 81
- **Severity:** MODERATE
- **Description:** The code `q.split()[0].upper() if q.split() else ""` calls `q.split()` twice. While functionally correct, if `query` is only whitespace, `q` (after `.strip()`) will be empty, `q.split()` returns `[]`, and `first_word` becomes `""`. This falls through to the "not in allowed" check correctly. However, the doubled `.split()` call is wasteful. More importantly, an all-whitespace query reaches the database block with an empty string, which is wasteful.
- **Suggested Fix:**
  ```python
  if not q:
      return SqlResult(text="ERROR: Empty query.", error=True)
  first_word = q.split(maxsplit=1)[0].upper()
  ```

### LOW-002: Error Message Leaks Database Internal Details

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/sql_executor.py`
- **Lines:** 148
- **Severity:** LOW
- **Description:** The error message `f"SQL ERROR: {e}"` passes the raw database exception to the caller, which is eventually sent to the Claude API as a tool result. This can leak internal details like table names, column names, server versions, or connection strings in error messages.
- **Suggested Fix:** Log the full exception but return a sanitized message:
  ```python
  logger.exception("SQL execution error for query: %s", q[:200])
  return SqlResult(text="SQL ERROR: Query execution failed. Check query syntax.", error=True)
  ```

---

## 3. `server/core/agent.py`

### CRITICAL-002: Infinite Loop Risk -- No Iteration Cap on Tool-Use Loop

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py`
- **Lines:** 519-614
- **Severity:** CRITICAL
- **Description:** The agentic `while True` loop at line 519 has no iteration cap. If the Claude API consistently returns `stop_reason == "tool_use"`, the loop will continue indefinitely. This could happen if:
  - The model enters a tool-use cycle (e.g., repeatedly calling SQL to fix a query error)
  - A logic error causes tool results to always prompt another tool call
  - The API returns malformed responses

  Each iteration incurs API costs (tokens), SQL execution, and server resources. A single request could rack up unlimited API costs.
- **Suggested Fix:** Add a maximum iteration counter:
  ```python
  MAX_TOOL_LOOPS = 15
  loop_count = 0
  while True:
      loop_count += 1
      if loop_count > MAX_TOOL_LOOPS:
          yield _sse("error", {
              "error": "Maximum tool-use iterations exceeded",
              "code": "MAX_ITERATIONS",
              "recoverable": False,
          })
          break
      ...
  ```

### CRITICAL-003: Conversation History Grows Without Bound

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py`
- **Lines:** 249, 504, 579, 608
- **Severity:** CRITICAL
- **Description:** Messages are appended to `self.messages` with no limit. Over a long session:
  1. The messages list grows indefinitely
  2. Each API call sends the entire history, causing token counts to explode
  3. Eventually the request will exceed the Claude API's context window limit (200K tokens), causing API errors
  4. Memory usage on the server grows without bound

  With tool-use loops, each iteration adds 2+ messages (assistant + user/tool_result), so a single user message can add dozens of history entries.
- **Suggested Fix:** Implement a message truncation strategy. When the history exceeds a threshold, either:
  - Summarize older messages and replace them with a summary
  - Drop older message pairs (keeping the first few for context)
  - Track approximate token count and trim when approaching the limit

### CRITICAL-004: BadRequestError Retry Creates Identical Request

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py`
- **Lines:** 552-563
- **Severity:** CRITICAL
- **Description:** When a `BadRequestError` is caught (line 552), the code retries with an identical call -- same model, same system blocks, same tools, same messages. The comment says "Haiku / older model -- no thinking support, retry without" but no parameters are actually changed between the first and second attempt. The `thinking` parameter is never explicitly passed in either call. If the first call fails with a `BadRequestError` for any other reason (e.g., messages too long, invalid tool schema), the retry will also fail, and the resulting exception will be caught by the outer `except` blocks, potentially masking the real error.
- **Suggested Fix:** Either properly differentiate the retry (e.g., by disabling thinking via a parameter), or handle `BadRequestError` without a blind retry:
  ```python
  except anthropic.BadRequestError as exc:
      if "thinking" in str(exc).lower():
          # Retry without thinking parameter
          ...
      else:
          raise  # Let outer handler deal with it
  ```

### MODERATE-004: `generated_files` List is Never Reset Between Messages

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py`
- **Lines:** 258
- **Severity:** MODERATE
- **Description:** `self.generated_files` is initialized in `__init__` but never cleared between calls to `send_message()`. If the same `SealineAgent` instance processes multiple messages (which happens in the Teams sync flow), the `generated_files` list accumulates all files from all messages. The route handler in `messages.py` (line 167) appends `agent.generated_files` to the session, so files from message N would be re-appended when message N+1 is processed.
- **Suggested Fix:** Clear the list at the start of `send_message()`:
  ```python
  def send_message(self, user_text: str):
      self.generated_files = []  # Reset for this message
      ...
  ```

### MODERATE-005: Usage Counters Overwrite (Not Accumulate) in Session Persistence

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
- **Lines:** 159-163
- **Severity:** MODERATE
- **Description:** The route handler copies agent usage counters to the session with simple assignment:
  ```python
  session.total_input_tokens = agent.total_input_tokens
  session.total_output_tokens = agent.total_output_tokens
  ```
  However, the agent is created fresh for each message (line 129) with a new messages copy. Since the agent's `__init__` sets counters to 0, the agent's counters only reflect the current message. The session's cumulative counters are **overwritten** rather than accumulated. After the second message, the session's `total_input_tokens` will only reflect the tokens from the second message, not the sum of both.
- **Suggested Fix:** Accumulate instead of overwrite:
  ```python
  session.total_input_tokens += agent.total_input_tokens
  session.total_output_tokens += agent.total_output_tokens
  session.cache_hits += agent.cache_hits
  session.sql_calls += agent.sql_calls
  ```

### MODERATE-006: Session State Not Updated on Error Path

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py`
- **Lines:** 192-236
- **Severity:** MODERATE
- **Description:** When an exception occurs in the message processing generator (caught at line 192), the session's `messages` list is not updated. This means if the agent partially processed the message (e.g., appended the user message and one assistant response before crashing), the session's messages remain from before the request. On the next request, the user's message and any partial tool results are lost, but the user message was already consumed by the agent's internal copy. This creates an inconsistent state.
- **Suggested Fix:** Either update the session messages from the agent even on error (if the agent exists), or wrap the agent's message mutation in a way that can be rolled back.

### MODERATE-007: Duplicate `message_start` Event Emitted to Client

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py` (line 506-509) and `/Users/peter_parker/Desktop/Sealine-Database/server/routes/messages.py` (line 123-126, 151)
- **Severity:** MODERATE
- **Description:** The route handler emits its own `message_start` event (line 123) and then filters out the agent's `message_start` (line 151). However, both use different `message_id` values -- the route generates one at line 107, and the agent generates another at line 501. The agent's `message_end` event is also suppressed, but the usage data in the agent's `message_end` is lost and replaced by the route's version. This is not a bug per se, but the agent generates a `message_id` (line 501) that is never exposed to the client, which is wasteful and could cause confusion during debugging.
- **Suggested Fix:** Either pass the `message_id` from the route into the agent, or remove the `message_id` generation from the agent entirely.

### LOW-003: Tool Definitions Always Include File Tools Even When DB is Disabled

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py`
- **Lines:** 310-318
- **Severity:** LOW
- **Description:** The `_tools()` method always appends plot, PDF, and Excel tool definitions regardless of any configuration. Even if the file generator is unavailable (`_FILE_TOOLS_AVAILABLE = False`), fallback tool definitions are still sent to the Claude API. When Claude invokes these tools, the user gets a "not available yet" message. This wastes API tokens on tool definitions and may confuse the model.
- **Suggested Fix:** Add a configuration flag or check to conditionally include file tools.

### LOW-004: The `or True` in `_system_blocks` Renders the Check Meaningless

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py`
- **Line:** 278
- **Severity:** LOW
- **Description:**
  ```python
  if _FILE_TOOLS_AVAILABLE or True:  # tools defined even without impl
  ```
  The `or True` makes the condition always true, making the `_FILE_TOOLS_AVAILABLE` check dead code. While the comment explains the intent, this is confusing and should be simplified.
- **Suggested Fix:** Either remove the condition entirely or remove the `or True`:
  ```python
  # File tools are always defined (fallbacks exist)
  tool_instructions.append(...)
  ```

---

## 4. `server/core/context_loader.py`

### MODERATE-008: No File Size Limit on Loaded Markdown Files

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/context_loader.py`
- **Lines:** 33-38
- **Severity:** MODERATE
- **Description:** The loader reads every `.md` file found in the search directory with no limit on individual file size or total concatenated size. A single large markdown file (or many small ones) could result in a system prompt that exceeds the Claude API's context window. This would cause API errors or extreme token costs on every single message.
- **Suggested Fix:** Add configurable limits:
  ```python
  MAX_FILE_SIZE = 100_000  # 100KB per file
  MAX_TOTAL_SIZE = 500_000  # 500KB total

  for path in paths:
      size = os.path.getsize(path)
      if size > MAX_FILE_SIZE:
          logger.warning("Skipping oversized file %s (%d bytes)", path, size)
          continue
      ...
  ```

### MODERATE-009: Symlink Traversal Could Load Files Outside Search Root

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/context_loader.py`
- **Lines:** 23-24
- **Severity:** MODERATE
- **Description:** The `glob.glob()` with `recursive=True` follows symlinks. A symlink inside the `memory/` directory could point to files outside the intended directory tree (e.g., `/etc/passwd.md` or application config files). While the `.md` extension filter limits exposure, this is a directory traversal risk.
- **Suggested Fix:** Validate that resolved paths are within the search root:
  ```python
  real_root = os.path.realpath(search_root)
  for path in paths:
      real_path = os.path.realpath(path)
      if not real_path.startswith(real_root):
          logger.warning("Skipping symlink escape: %s -> %s", path, real_path)
          continue
  ```

### LOW-005: No Deduplication of Loaded Files

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/context_loader.py`
- **Lines:** 24
- **Severity:** LOW
- **Description:** If symlinks or mount points cause the same file to appear under multiple paths, it will be loaded multiple times, inflating the system prompt with duplicate content.
- **Suggested Fix:** Track loaded files by their `os.path.realpath()` and skip duplicates.

---

## 5. `server/core/file_generator.py`

### CRITICAL-005: Matplotlib Figure Leak on Pie Chart Path

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/file_generator.py`
- **Lines:** 279-290
- **Severity:** CRITICAL
- **Description:** In the pie chart branch of `_plot_static`, a new `fig, ax` pair is created (line 282) after the original `ax` is removed (line 281), but the **original** `fig` created at line 259 is never closed. Each call leaks a matplotlib figure, consuming memory that is never freed. Under sustained load, this will cause memory exhaustion:
  ```python
  fig, ax = plt.subplots(figsize=(10, 6))  # Line 259: original fig
  ...
  elif plot_type == "pie":
      ax.remove()
      fig, ax = plt.subplots(figsize=(10, 6))  # Line 282: rebinds fig, original leaks
  ```
- **Suggested Fix:** Close the original figure before creating a new one:
  ```python
  elif plot_type == "pie":
      ax.remove()
      plt.close(fig)  # Close the original figure
      fig, ax = plt.subplots(figsize=(10, 6))
  ```

### MODERATE-010: `generate_plot` Returns Error Dict Instead of Raising

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/file_generator.py`
- **Lines:** 229-231
- **Severity:** MODERATE
- **Description:** When `generate_plot` catches an exception, it returns `{"error": str(exc)}` instead of raising. The caller in `agent.py` (line 369) calls `self.generated_files.append(file_info)`, which would append this error dict to the generated files list. Subsequent code that tries to access `file_info.get("file_type", "")` (line 372) would get `None` (since the error dict has no `file_type` key), and the SSE event would contain incomplete metadata.
- **Suggested Fix:** Either raise the exception (the caller already has a try/except) or check the return value for an "error" key before appending:
  ```python
  file_info = _generate_plot(...)
  if "error" in file_info:
      raise RuntimeError(file_info["error"])
  self.generated_files.append(file_info)
  ```

### MODERATE-011: Path Traversal in Filename Parameter

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/file_generator.py`
- **Lines:** 503, 694
- **Severity:** MODERATE / SECURITY
- **Description:** The `filename` parameter for PDF and Excel generation is passed through `_slugify()`, which strips special characters. However, `_slugify` does not strip directory separators on all platforms. On certain inputs, the `os.path.join(file_store_path, out_filename)` could potentially write outside the intended directory if `_slugify` doesn't catch all traversal patterns. While the current `_slugify` implementation is relatively safe (stripping non-word characters), it relies on regex behavior that could vary.
- **Suggested Fix:** Add an explicit check that the final path is within the store directory:
  ```python
  full_path = os.path.join(file_store_path, out_filename)
  if not os.path.abspath(full_path).startswith(os.path.abspath(file_store_path)):
      raise ValueError("Invalid filename: path traversal detected")
  ```

### MODERATE-012: Heatmap Crashes if `values` Key Used for Both Matrix and Flat List

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/file_generator.py`
- **Lines:** 292-301
- **Severity:** MODERATE
- **Description:** The heatmap handler uses `data.get("values", [])` at line 262 (shared with other plot types), but for heatmap, `values` is expected to be a 2D matrix. If Claude provides data in the documented heatmap format `{"labels_x": [...], "labels_y": [...], "values": [[...]]}`, this works. But the `values` variable is already assigned at line 262 from the shared extraction. If the heatmap path is reached, line 295 does `np.array(values, dtype=float)`, which would work for 2D lists. However, if `values` is not a valid 2D array (e.g., ragged arrays or non-numeric data), `np.array(..., dtype=float)` raises a `ValueError` that propagates up.
- **Suggested Fix:** Add explicit validation:
  ```python
  try:
      matrix = np.array(values, dtype=float)
      if matrix.ndim != 2:
          return {"error": "Heatmap values must be a 2D array"}
  except (ValueError, TypeError) as e:
      return {"error": f"Invalid heatmap data: {e}"}
  ```

### MODERATE-013: XSS in PDF HTML Template via Insufficient Escaping

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/file_generator.py`
- **Lines:** 559, 663-670
- **Severity:** MODERATE / SECURITY
- **Description:** The `_esc()` function provides minimal HTML escaping (replacing `&`, `<`, `>`, `"`). However, single quotes (`'`) are not escaped. In certain template contexts (e.g., inside HTML attributes delimited by single quotes), this could allow injection. While the PDF is generated server-side and rendered by WeasyPrint (which is less susceptible to script injection), the HTML is also potentially served directly if stored as an intermediate file.
- **Suggested Fix:** Use Python's `html.escape()` from the standard library, which handles all special characters:
  ```python
  import html
  def _esc(text: str) -> str:
      return html.escape(text, quote=True)
  ```

### LOW-006: `_is_numeric` Treats Comma-Formatted Strings as Numbers

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/file_generator.py`
- **Lines:** 184-194
- **Severity:** LOW
- **Description:** The function strips commas before checking `float()`:
  ```python
  float(value.replace(",", ""))
  ```
  This means strings like `"1,2,3"` (which could be a multi-value field) are treated as numeric (`123.0`). This could cause incorrect column alignment in PDF reports.
- **Suggested Fix:** Only strip commas that appear as thousands separators (e.g., using a regex that validates the pattern):
  ```python
  cleaned = re.sub(r"(?<=\d),(?=\d{3})", "", value)
  float(cleaned)
  ```

### LOW-007: No Validation on Empty `columns` or `rows` for PDF/Excel

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/file_generator.py`
- **Lines:** 488-524, 678-782
- **Severity:** LOW
- **Description:** If `columns` is an empty list or `rows` is empty, the generators will produce files with no content. While not strictly a bug, edge cases like mismatched column/row lengths (e.g., 3 columns but rows with 5 elements) are not validated and could produce garbled output.
- **Suggested Fix:** Add validation at the top of each generator:
  ```python
  if not columns:
      return {"error": "columns list must not be empty"}
  ```

---

## 6. `server/sessions/store.py`

### CRITICAL-006: Race Condition in `cleanup_expired` -- Session Modified After Lock Release

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/sessions/store.py`
- **Lines:** 211-236
- **Severity:** CRITICAL
- **Description:** The `cleanup_expired` method collects expired session IDs while holding the lock, then releases the lock and calls `self.delete(sid)` for each expired ID. Between releasing the lock and calling `delete()`, another thread could:
  1. Send a message to the session (updating `last_active` to now, making it no longer expired)
  2. Create new files in the session that would be deleted

  The `delete()` call acquires the lock again to pop the session, but by then the session might have been reactivated by user activity. This would delete an active session out from under a user.
  ```python
  with self._lock:
      for sid, session in self._sessions.items():
          if elapsed_hours >= ttl:
              expired_ids.append(sid)
  # Lock released here -- session could be reactivated by another thread
  for sid in expired_ids:
      self.delete(sid)  # Deletes potentially-reactivated session
  ```
- **Suggested Fix:** Re-check `last_active` inside the `delete` call, or perform the deletion inside the lock:
  ```python
  def cleanup_expired(self, ttl_hours=None):
      ttl = ttl_hours if ttl_hours is not None else self._ttl_hours
      now = datetime.now(timezone.utc)
      expired_sessions = []
      with self._lock:
          expired_ids = [
              sid for sid, s in self._sessions.items()
              if (now - s.last_active).total_seconds() / 3600 >= ttl
          ]
          for sid in expired_ids:
              expired_sessions.append(self._sessions.pop(sid))
      # Now clean up files outside the lock (safe, sessions are already removed)
      for session in expired_sessions:
          self._cleanup_session_files(session)
      return len(expired_sessions)
  ```

### CRITICAL-007: Double File Deletion -- Route and Store Both Delete Files

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/sessions/store.py` (line 240-250) and `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py` (lines 148-155, 159)
- **Severity:** MODERATE (upgraded from LOW due to potential race)
- **Description:** The `DELETE /api/sessions/<session_id>` route handler manually deletes all files associated with the session (lines 148-155), then calls `store.delete(session_id)` (line 159), which **also** deletes the session's files via `_cleanup_session_files()`. This results in double-deletion attempts. While `os.remove()` on a non-existent file raises `FileNotFoundError` (caught by the store's handler), this is wasteful and confusing. More critically, if new files were generated between the route's deletion loop and the store's deletion, those files would be deleted without the route being aware.
- **Suggested Fix:** Remove the file deletion from the route handler and let the store handle it exclusively:
  ```python
  # In routes/sessions.py, remove lines 148-155 and just call:
  store.delete(session_id)
  ```

### MODERATE-014: Session `touch()` is Never Called

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/sessions/store.py`
- **Lines:** 85-87
- **Severity:** MODERATE
- **Description:** The `Session.touch()` method exists to update `last_active`, but it is never called anywhere in the codebase. Instead, `messages.py` line 164 directly sets `session.last_active = datetime.now(timezone.utc)`. The `touch()` method is dead code. More importantly, `last_active` is only updated after a successful message stream -- if the user creates a session and never sends a message, or sends a message that errors out, `last_active` remains at session creation time. This means sessions that encounter errors early will be cleaned up based on creation time, which is correct behavior but undocumented.
- **Suggested Fix:** Use `session.touch()` consistently, and call it at the start of message processing (not just the end) to prevent premature cleanup of sessions with long-running queries.

### LOW-008: `to_metadata()` and GET Route Duplicate User-Turn Counting Logic

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/sessions/store.py` (lines 91-102) and `/Users/peter_parker/Desktop/Sealine-Database/server/routes/sessions.py` (lines 90-102)
- **Severity:** LOW
- **Description:** The logic for counting user turns (excluding tool_result messages) is duplicated between `Session.to_metadata()` and the `get_session()` route handler. These implementations could diverge if one is updated without the other. The route handler does not use `to_metadata()` and reimplements the count.
- **Suggested Fix:** Use `session.to_metadata()` in the route handler, or extract the counting logic into a single method.

### LOW-009: Background Cleanup Thread Cannot Be Stopped

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/sessions/store.py`
- **Lines:** 252-261
- **Severity:** LOW
- **Description:** The cleanup loop runs `while True` with no shutdown mechanism. While the thread is a daemon (it will die when the main process exits), there is no way to gracefully stop it during testing or application teardown. This can cause issues with test frameworks that expect clean shutdown.
- **Suggested Fix:** Use a `threading.Event` for shutdown:
  ```python
  self._stop_event = threading.Event()

  def _cleanup_loop(self, interval):
      while not self._stop_event.wait(interval):
          try:
              self.cleanup_expired()
          except Exception:
              logger.exception("Error in session cleanup loop")

  def shutdown(self):
      self._stop_event.set()
  ```

### LOW-010: Session Data Not Protected Against Concurrent Message Processing

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/sessions/store.py`
- **Lines:** 52-87
- **Severity:** LOW (for V1 with 1-5 users, MODERATE at scale)
- **Description:** The `Session` dataclass has no locking on its fields. If two concurrent requests target the same session (e.g., user sends two messages rapidly), both would read the same `messages` list, create separate agents, and both would try to write back `session.messages` at the end. The second write would overwrite the first, losing one conversation branch entirely. The `SessionStore` lock only protects the `_sessions` dict, not individual session objects.
- **Suggested Fix:** Add a per-session lock, or enforce that only one message can be processed per session at a time:
  ```python
  @dataclass
  class Session:
      _processing_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
  ```

---

## 7. Cross-Cutting Issues

### SECURITY-005: API Key Exposure in Error Messages

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py`
- **Lines:** 616-624
- **Severity:** SECURITY
- **Description:** The `AuthenticationError` handler includes `{exc}` in the error message sent to the client:
  ```python
  "error": f"Authentication error: {exc}",
  ```
  Anthropic's `AuthenticationError` exceptions may include the (partial) API key in their message text. This would be streamed to the frontend and visible in browser developer tools.
- **Suggested Fix:** Use a generic error message:
  ```python
  "error": "Anthropic API authentication failed. Check server configuration.",
  ```

### SECURITY-006: No Rate Limiting or Input Size Validation

- **File:** Multiple files
- **Severity:** SECURITY
- **Description:** There is no rate limiting on the `/api/sessions/<id>/messages` endpoint, and no validation on the size of the `message` field in the request body. A client could:
  1. Send thousands of rapid requests, consuming API credits
  2. Send extremely large messages (megabytes of text), which would be forwarded to the Claude API
  3. Create unlimited sessions via `POST /api/sessions`
- **Suggested Fix:** Add Flask middleware for rate limiting (e.g., `flask-limiter`) and validate input size:
  ```python
  MAX_MESSAGE_LENGTH = 50_000  # characters
  if len(message) > MAX_MESSAGE_LENGTH:
      return _error("Message too long", "INVALID_REQUEST", 400)
  ```

### SECURITY-007: File Download Endpoint Has No Authentication

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/routes/files.py`
- **Lines:** 59-111
- **Severity:** SECURITY
- **Description:** The `GET /api/files/<file_id>` endpoint serves generated files to anyone who knows the file ID. While file IDs are UUID-based (8 hex chars = ~4 billion possibilities), they are relatively short and could be brute-forced. There is no check that the requesting user owns the session that generated the file. Any user can download any other user's generated reports if they guess or obtain the file ID.
- **Suggested Fix:** Require a session_id parameter or cookie and validate that the file belongs to that session:
  ```python
  @files_bp.route("/sessions/<session_id>/files/<file_id>", methods=["GET"])
  def download_file(session_id: str, file_id: str):
      session = store.get(session_id)
      # Verify file belongs to this session
  ```

---

## 8. Additional Observations

### Observation 1: Anthropic Client Created Twice

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/app.py` (line 89) and `/Users/peter_parker/Desktop/Sealine-Database/server/core/agent.py` (line 236)
- **Description:** The app factory creates a shared Anthropic client (app.py line 89) but it is never passed to the agent. Instead, every `SealineAgent` instance creates its own client (agent.py line 236). The shared client in `app.py` is stored in `app.config["ANTHROPIC_CLIENT"]` but never used. This wastes resources (each client creates an httpx connection pool) and means the shared client configuration is dead code.

### Observation 2: File Store Path Mismatch Risk

- **File:** Multiple
- **Description:** `file_generator.py` defaults to `"./tmp/files"` for its `file_store_path`, while `app.py` constructs an absolute path (`server/tmp/files`). If any code path calls a generator function without passing the correct `file_store_path`, files would be written to a relative `./tmp/files` directory instead of the intended location, potentially splitting generated files across two directories.

### Observation 3: `gunicorn` with Multiple Workers and In-Memory Session Store

- **File:** `/Users/peter_parker/Desktop/Sealine-Database/server/config.py` (line 18: `WORKERS: 2`)
- **Description:** With `WORKERS=2`, gunicorn pre-forks 2 worker processes. Each process has its own `SessionStore` in memory. A session created in Worker 1 will not be visible to Worker 2. Requests from the same user could be routed to different workers, causing `SESSION_NOT_FOUND` errors. This is a fundamental architectural issue for multi-worker deployments.
- **Suggested Fix:** Either set `WORKERS=1`, use gunicorn's `--preload` option (which does not solve the fundamental issue since forked processes have independent memory), or switch to a shared session store (Redis, database).

---

## Priority Remediation Order

1. **SECURITY-001** (hardcoded credentials) -- Immediate: rotate the credentials and remove defaults
2. **SECURITY-003** (EXEC allowed in SQL) -- Immediate: remove EXEC/EXECUTE from allowed keywords
3. **CRITICAL-001** (connection leak) -- High: will cause production outages under load
4. **CRITICAL-006** (cleanup race condition) -- High: can delete active sessions
5. **CRITICAL-002** (infinite tool loop) -- High: unbounded API cost exposure
6. **CRITICAL-005** (matplotlib figure leak) -- High: memory leak under sustained charting use
7. **CRITICAL-003** (unbounded message history) -- Medium: will eventually hit API limits
8. **CRITICAL-004** (identical retry on BadRequestError) -- Medium: masks real errors
9. **SECURITY-004** (multi-statement SQL injection) -- High: use read-only DB user
10. **SECURITY-002** (SSL bypass) -- Medium: MitM risk on API communication
11. **MODERATE-005** (usage counter overwrite) -- Medium: data accuracy
12. **SECURITY-006** (no rate limiting) -- Medium: abuse protection
13. All remaining MODERATE and LOW issues
