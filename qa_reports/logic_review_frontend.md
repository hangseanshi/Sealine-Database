# Frontend Logic Review Report

**Project:** Sealine Data Chat
**Reviewer:** Senior Frontend QA Engineer
**Date:** 2026-03-11
**Scope:** All React frontend source files (`client/src/`)
**Reference:** PRD.md (Sections 7, 6.2)

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 5     |
| MODERATE | 12    |
| LOW      | 11    |

---

## CRITICAL BUGS

### CRIT-01: Race condition when sending a message to a newly created session

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 234-301
**Component:** `handleNewChat` + `handleSendMessage`

**Description:**
`handleNewChat` calls `setActiveSessionId(data.session_id)` (line 244), which is an asynchronous React state update. If a user rapidly clicks "New Chat" and then types and sends a message before the next render cycle completes, `handleSendMessage` reads `activeSessionId` from its closure (line 276). Because `handleSendMessage` has `activeSessionId` in its dependency array (line 300), it captures a stale value until the component re-renders. The user's message could be sent to the old session or discarded because `activeSessionId` is still `null` or points to the previous session.

However, the SSE callbacks use `activeSessionRef.current` (line 26-27), which is updated synchronously on every render. This means the SSE response events could be routed to the correct new session while the user message was sent to the wrong one, causing a mismatch between the displayed user message and the agent response.

**Impact:** Lost user message or message sent to wrong session.

**Suggested Fix:**
Have `handleSendMessage` also use `activeSessionRef.current` instead of the closed-over `activeSessionId`, or alternatively, make `handleNewChat` return the new session ID and have the "send first message" flow pass the session ID explicitly rather than relying on state.

---

### CRIT-02: `handleDeleteSession` has stale closure over `activeSessionId`

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 251-267

**Description:**
`handleDeleteSession` depends on `activeSessionId` (line 266), but the `setSessions` updater function accesses `activeSessionId` from its closure (line 260), not from the latest state. If multiple delete operations happen quickly, or if a session switch and delete happen near-simultaneously, `activeSessionId` inside the closure could be stale.

Additionally, calling `setActiveSessionId` from within a `setSessions` updater function (line 261) is a problematic pattern. The `setSessions` updater should be a pure function of `prev`, but here it has a side-effect of calling another state setter. While React technically allows this, it can lead to unexpected batching behavior and ordering issues.

**Impact:** After deleting a session, the app could switch to the wrong session or remain pointing at a deleted session, leaving the UI in a broken state.

**Suggested Fix:**
Move the `activeSessionId` check outside the `setSessions` updater:
```js
const handleDeleteSession = useCallback(async (sessionId) => {
  try { await deleteSession(sessionId); } catch {}
  setSessions((prev) => prev.filter((s) => s.id !== sessionId));
  // Handle active session switch separately
  if (activeSessionRef.current === sessionId) {
    const remaining = sessions.filter((s) => s.id !== sessionId);
    setActiveSessionId(remaining.length > 0 ? remaining[0].id : null);
  }
}, [sessions]);
```
Or use `activeSessionRef.current` and compute from the updated list.

---

### CRIT-03: `onTextDelta` can update the wrong message when multiple agent messages exist

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 50-71

**Description:**
`onTextDelta` finds the **last** message with `type === 'agent'` and appends to it (line 60). But it does NOT verify that the found agent message has `isStreaming: true`. If the stream delivers events in this order:
1. `message_start` -> adds agent message (isStreaming: true)
2. `message_end` -> sets isStreaming: false
3. Backend sends another `message_start` + `text_delta` (e.g., in a multi-turn agentic loop)

Between steps 2 and 3, if a `text_delta` arrives late or out of order (network buffering), it will append text to the previous, already-completed agent message because it is still the last `type === 'agent'` message. Even in normal flow, this is fragile because it relies on the last agent message always being the streaming one, but `thinking`, `sql`, `file`, and `plot` messages can be interspersed after the agent message was added.

**Impact:** Text content appended to the wrong message bubble, corrupting the displayed conversation.

**Suggested Fix:**
`onTextDelta` should find the last agent message that has `isStreaming: true`, not just the last agent message:
```js
if (messages[i].type === 'agent' && messages[i].isStreaming) {
```

---

### CRIT-04: No abort/cleanup of SSE stream when switching sessions

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/hooks/useSSE.js`
**Lines:** 28-107
**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 269-272

**Description:**
When the user switches sessions (clicks a different session in the sidebar via `handleSelectSession`), the active SSE stream is NOT aborted. The `useSSE` hook only aborts a previous stream when a NEW `sendMessage` is called (line 30-32). If the user switches sessions while a response is still streaming:

1. `activeSessionId` changes, so `activeSessionRef.current` updates
2. The still-active SSE stream continues delivering events
3. SSE callbacks read `activeSessionRef.current` which now points to the NEW session
4. Text deltas and tool results from the OLD session's response are injected into the NEW session's message array
5. The new session gets corrupted with partial responses from the old session

**Impact:** Cross-session data contamination. Agent responses from one session appear in another session, corrupting the chat history.

**Suggested Fix:**
Expose an `abort()` function from `useSSE` and call it in `handleSelectSession`. Or, modify SSE callbacks to include the `sessionId` they are responding to and verify it matches the current active session before applying state updates.

---

### CRIT-05: Using array index as React key for messages causes rendering corruption

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/ChatArea.jsx`
**Lines:** 63-127

**Description:**
All messages use `key={idx}` (array index) as their React key. When messages are added to the array during streaming (agent message added, then sql block inserted, then more text), React uses the key to determine which components to re-render vs. reuse. Since messages are always appended (not inserted in the middle), this is less catastrophic than it could be, but it causes problems in specific scenarios:

1. If `ThinkingBlock` has internal state (`expanded`), and a new thinking message is added at a different index (e.g., the previous thinking block was at index 2, now at index 2 there's a different message), React will reuse the component at that index with its old state, causing wrong expansion state.
2. `SqlBlock` also has internal `expanded` state. If the message array is ever re-ordered or filtered, the state would be applied to the wrong SQL block.
3. `memo()` on `MessageBubble` and `SqlBlock` becomes less effective because keys change when messages shift.

**Impact:** Incorrect component state persistence, particularly for collapsible SQL blocks and thinking blocks. UI shows wrong expanded/collapsed state for the wrong message.

**Suggested Fix:**
Assign a unique ID to each message when it is created (e.g., `crypto.randomUUID()` or a simple incrementing counter) and use that as the key instead of array index.

---

## MODERATE ISSUES

### MOD-01: SSE multi-line `data:` field parsing strips significant whitespace

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/hooks/useSSE.js`
**Lines:** 135-136

**Description:**
When parsing SSE data lines, the code does `line.slice(5).trim()` (line 136). The SSE specification states that if there is a space after the colon, only that single space should be removed. Using `.trim()` removes ALL leading and trailing whitespace from each data line. If the server sends data with intentional leading/trailing whitespace (e.g., indented JSON or multi-line content), it will be silently stripped.

Then on line 147, `dataLines.join('\n')` reassembles multi-line data. If the original JSON was split across multiple `data:` lines, the trimming could break JSON parsing by removing spaces within string values that happen to be at the start or end of a line.

**Impact:** Potential JSON parse failures or data corruption for multi-line data fields.

**Suggested Fix:**
Per the SSE spec, strip only a single leading space:
```js
dataLines.push(line.slice(5).replace(/^ /, ''));
```

---

### MOD-02: No cleanup on component unmount for SSE streams

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/hooks/useSSE.js`
**Lines:** 21-110

**Description:**
The `useSSE` hook does not include a cleanup effect to abort the active stream when the component unmounts. If the `App` component unmounts (e.g., due to a parent error boundary, React StrictMode double-mount in development, or future routing changes), the `fetch` read loop continues running in the background. The callbacks will attempt to call `setSessions` on an unmounted component, causing React "Can't perform a state update on an unmounted component" warnings and potential memory leaks.

**Impact:** Memory leak and React state update warnings on unmounted component.

**Suggested Fix:**
Add a cleanup effect:
```js
useEffect(() => {
  return () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  };
}, []);
```

---

### MOD-03: `onToolResult` can fail to match its corresponding `onToolStart` message

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 114-137

**Description:**
`onToolResult` finds the last SQL message with `isRunning: true` (line 124). But the matching is only by position (last running SQL block), not by a correlation ID. If the backend sends two `tool_start` events before their corresponding `tool_result` events (which could happen in a multi-tool agentic loop), the results would be matched in LIFO order instead of FIFO, potentially attaching the wrong result to the wrong query.

The PRD (Section 6.2) does not define a `tool_call_id` field for correlation, so this is a gap in both the spec and implementation.

**Impact:** SQL query results displayed against the wrong query in the UI.

**Suggested Fix:**
Add a unique identifier to `tool_start` and `tool_result` events (like a `tool_call_id`) and match on that. If the backend cannot be changed, at minimum match on the `tool` name in addition to `isRunning` status.

---

### MOD-04: No user-facing error state when session creation fails

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 234-249

**Description:**
`handleNewChat` catches errors on line 246-248 but only logs to `console.error`. The user receives no feedback that session creation failed. They see no new session appear in the sidebar and may not understand why. If the backend is down, every click on "New Chat" silently fails.

**Impact:** Users cannot tell that the system is unreachable. Poor UX.

**Suggested Fix:**
Add an error state and display a toast notification or error banner to the user when session creation fails.

---

### MOD-05: `error` field in PRD SSE error event is not `recoverable`-aware

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 191-207
**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/ChatArea.jsx`
**Lines:** 113-119

**Description:**
The PRD (Section 6.2) defines the error event as having fields `error` and `code`, but does NOT include `recoverable`. The `onError` callback in App.jsx adds `recoverable` to the message object (line 202), and the catch block in useSSE.js fabricates a `recoverable: true` field (line 100). However, the `ChatArea` error rendering (lines 113-119) completely ignores the `recoverable` flag. There is no UI difference between a recoverable and non-recoverable error -- no retry button, no guidance, no distinction.

Additionally, the PRD error spec does not include `recoverable`, so the client is adding a non-standard field and then not using it.

**Impact:** Users see the same generic error display regardless of whether the error is recoverable or terminal.

**Suggested Fix:**
Either remove the `recoverable` field since it is unused, or implement a retry button for recoverable errors and show different messaging for non-recoverable errors.

---

### MOD-06: `isStreaming` flag is global, not per-session

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/hooks/useSSE.js`
**Line:** 22
**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 230, 343-344

**Description:**
The `isStreaming` boolean from `useSSE` is a single global flag. It is passed to `ChatArea` (line 338) and `InputBar` (line 343). This means:
1. If user is on Session A and sends a message, `isStreaming` becomes `true`.
2. User switches to Session B. The input bar is still disabled because `isStreaming` is still `true`.
3. The user cannot interact with Session B until Session A's stream finishes.

Combined with CRIT-04, this creates a situation where switching sessions leaves the UI in a broken half-state.

**Impact:** Input bar remains disabled on unrelated sessions during streaming.

**Suggested Fix:**
Track `isStreaming` per session (e.g., as a property on the session object), or abort the stream when switching sessions.

---

### MOD-07: Auto-scroll fires on every messages array reference change, not on content change

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/ChatArea.jsx`
**Lines:** 21-25

**Description:**
The auto-scroll `useEffect` depends on `session?.messages` (line 25). During streaming, every `text_delta` event creates a new messages array (via the spread operator in `onTextDelta`), triggering the scroll effect dozens of times per second. `scrollIntoView({ behavior: 'smooth' })` queues smooth scroll animations, but firing it this frequently can cause janky scrolling behavior, especially on lower-end hardware.

Additionally, if the user has manually scrolled up to read earlier messages, the auto-scroll will keep forcing them back to the bottom on every delta, making it impossible to review earlier content during streaming.

**Impact:** Janky scrolling during streaming; inability to scroll up to read earlier messages while agent is responding.

**Suggested Fix:**
Add a "stick to bottom" detection: only auto-scroll if the user is already near the bottom of the scroll container. Use `containerRef` to check scroll position before scrolling.

---

### MOD-08: `MessageBubble` is memoized but receives unstable `text` prop during streaming

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/MessageBubble.jsx`
**Lines:** 14, 41

**Description:**
`MessageBubble` is wrapped in `memo()`, but during streaming, the `text` prop changes on every `text_delta` event (potentially dozens of times per second). Since `text` is a new string each time, `memo()` does a shallow comparison and detects a change, causing a re-render every time. This means `memo()` adds overhead (the comparison) without providing any benefit during streaming.

Worse, `ReactMarkdown` (line 25) re-parses and re-renders the entire markdown AST on each delta. For long agent responses, this becomes progressively slower as the text grows — O(n) work on each of the O(n) deltas, resulting in O(n^2) total work for a single response.

**Impact:** Progressive performance degradation during streaming of long responses. UI may become sluggish or unresponsive.

**Suggested Fix:**
Consider deferring markdown rendering during streaming. While `isStreaming` is true, render the text as plain text or use a simpler incremental rendering approach. Only parse full markdown after `message_end` is received.

---

### MOD-09: `onMessageEnd` does not handle the case where no streaming agent message exists

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 209-227

**Description:**
`onMessageEnd` searches backward for an agent message with `isStreaming: true` (line 219). If no such message exists (e.g., the agent response consisted entirely of tool calls and file generations with no text), the loop completes without finding a match, and the message array is returned unchanged. This is not a crash, but it means `isStreaming` on useSSE transitions to `false` (via the `finally` block) while there might be an agent message that never got its `isStreaming` flag set to `false` if the `message_end` event was missed or not received.

If the `message_start` event was received (creating an agent bubble with `isStreaming: true`) but `message_end` was dropped (network issue), the streaming cursor animation continues indefinitely on that message.

**Impact:** Orphaned streaming cursor animation on messages if `message_end` is lost.

**Suggested Fix:**
In the `finally` block of `sendMessage` in useSSE.js, also dispatch a synthetic `onMessageEnd` to ensure all streaming flags are cleaned up. Or clear all `isStreaming` flags when the stream ends.

---

### MOD-10: Sessions are not loaded from server on page load

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 20-21

**Description:**
The `sessions` array starts empty (`useState([])`). There is no `useEffect` to load existing sessions from the server on mount. The PRD (Section 7.3, Session Sidebar) explicitly states: "Sessions are not persisted -- refreshing the browser loses the sidebar list." However, the server-side sessions DO persist (for up to 2 hours). This means if a user refreshes the page:
1. All client-side session references are lost
2. Server-side sessions still exist but are unreachable from the UI
3. The user must create new sessions, wasting server resources

While the PRD calls this out as acceptable for V1, it is worth noting that the `getSessionInfo` API function exists in `api.js` (line 66-71) but is never used anywhere in the application.

**Impact:** User loses all chat history on page refresh, even though server sessions are still alive.

**Suggested Fix:**
Either implement a `GET /api/sessions` endpoint to list active sessions and load them on mount, or document this limitation prominently in the UI.

---

### MOD-11: `handleSendMessage` sends the message via SSE but does not handle `sendMessage` rejection

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 274-301

**Description:**
`handleSendMessage` awaits `sendMessage(activeSessionId, text.trim())` on line 298. If the `sendMessage` promise rejects (network error, etc.), the error is NOT caught in `handleSendMessage`. The `sendMessage` function internally catches errors and calls `onError`, but the uncaught rejection from the `await` in `handleSendMessage` will propagate up. Since `handleSendMessage` is called from `InputBar`'s `handleSend`, the uncaught promise rejection will be swallowed by React's event handler.

However, the user message was already added to the session (lines 279-295) BEFORE `sendMessage` is called. So the user sees their message in the chat, but if the stream fails, there's no indication that the message was never processed. The user may think the agent is simply slow.

**Impact:** User message appears sent but the agent never responds, with no visible error.

**Suggested Fix:**
Wrap the `await sendMessage` in a try/catch, and on failure, either remove the user message or add an error message to the session.

---

### MOD-12: `SqlBlock` collapsed by default means user misses SQL execution feedback

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/SqlBlock.jsx`
**Lines:** 14

**Description:**
The PRD (Section 7.3) specifies "Default state: collapsed" for SQL blocks, and the implementation matches. However, when the SQL block first appears (via `tool_start`), the "Running..." spinner is only visible inside the collapsed body (lines 72-86), NOT in the header. The header does show "Running..." text (lines 24-29), but only the label text -- the spinner is inline and may not be visually prominent.

When the result arrives, the header shows a small green checkmark (lines 31-41). But if the user doesn't notice the SQL block was added (because it's collapsed and small), they may not realize a query ran and completed.

This is actually handled reasonably in the current code (the header does show status), but the running spinner in the header (`sql-running` class) uses `marginLeft: 0` which means it's not separated from "SQL Query" text.

**Impact:** Minor. SQL execution status is technically visible but could be more prominent.

**Suggested Fix:**
Add `margin-left: 8px` to the `.sql-running` span, or auto-expand SQL blocks while they are running.

---

## LOW ISSUES

### LOW-01: No confirmation dialog before deleting a session

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/Sidebar.jsx`
**Lines:** 43-52

**Description:**
Clicking the delete button immediately triggers `onDeleteSession` with no confirmation. The delete button is small (hidden until hover), so accidental clicks are somewhat mitigated, but there is no way to undo a deletion. On mobile, where hover states don't exist, the delete button behavior may be inconsistent.

**Impact:** Accidental deletion of chat sessions with no undo.

**Suggested Fix:**
Add a `window.confirm()` dialog or implement a two-click confirmation pattern.

---

### LOW-02: No loading state while waiting for session creation

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/Sidebar.jsx`
**Lines:** 27-29

**Description:**
The "New Chat" button has no loading/disabled state while the `POST /api/sessions` request is in flight. If the backend is slow, the user may click it multiple times, creating multiple sessions.

**Impact:** Multiple duplicate sessions created from repeated clicks.

**Suggested Fix:**
Pass an `isCreating` prop to Sidebar and disable the button while the request is pending.

---

### LOW-03: `FileBadge` does not handle missing `downloadUrl`

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/FileBadge.jsx`
**Lines:** 27-35

**Description:**
If `downloadUrl` is `undefined` or `null` (e.g., server sends malformed `file_generated` event), the `<a>` tag will render with `href={undefined}`, which results in `href="undefined"` in the DOM. Clicking it navigates to a page called "undefined".

**Impact:** Broken download link if server sends incomplete data.

**Suggested Fix:**
Conditionally render the download button only when `downloadUrl` is truthy:
```js
{downloadUrl && (
  <a className="file-download-btn" href={downloadUrl} ...>
    Download
  </a>
)}
```

---

### LOW-04: `InlinePlot` does not handle image load errors

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/InlinePlot.jsx`
**Lines:** 20-25

**Description:**
The `<img>` tag has no `onError` handler. If the plot image URL is invalid, expired (past 24h TTL per PRD), or the server is unreachable, the browser shows a broken image icon with no explanation.

**Impact:** Broken image icon displayed in chat with no error message.

**Suggested Fix:**
Add an `onError` handler that displays a fallback message:
```js
const [imgError, setImgError] = useState(false);
// ...
{imgError ? (
  <div className="plot-error">Image could not be loaded</div>
) : (
  <img ... onError={() => setImgError(true)} />
)}
```

---

### LOW-05: `InlinePlot` image has no `width`/`height` attributes, causing layout shift

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/InlinePlot.jsx`
**Lines:** 20-25

**Description:**
The `<img>` tag has `loading="lazy"` but no explicit `width` or `height` attributes. When the image loads, it will cause a layout shift (CLS) as the browser recalculates the layout. This is especially noticeable during scrolling.

**Impact:** Layout shift when plot images load.

**Suggested Fix:**
Set a default aspect ratio via CSS or provide estimated dimensions.

---

### LOW-06: No keyboard accessibility for sidebar session items

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/Sidebar.jsx`
**Lines:** 33-53

**Description:**
Session items are `<div>` elements with `onClick` handlers. They are not focusable via keyboard (`Tab` key) and do not respond to `Enter` or `Space` key presses. The delete button IS a `<button>` and is keyboard-accessible, but the session selection itself is not.

**Impact:** Keyboard-only users cannot navigate or select sessions.

**Suggested Fix:**
Add `role="button"`, `tabIndex={0}`, and `onKeyDown` handler for Enter/Space to session items. Or use `<button>` elements instead of `<div>`.

---

### LOW-07: No keyboard accessibility for `InlinePlot` click-to-open

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/InlinePlot.jsx`
**Lines:** 14-16, 20-24

**Description:**
The plot image has an `onClick` handler to open the full-size image, but the `<img>` tag is not keyboard-focusable. Keyboard users cannot trigger the "open in new tab" action.

**Impact:** Keyboard users cannot open full-size plots.

**Suggested Fix:**
Wrap the image in a `<button>` or `<a>` tag, or add `tabIndex={0}` and `onKeyDown` to the `<img>`.

---

### LOW-08: `sidebar-overlay` div always rendered in DOM on desktop

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 313-316

**Description:**
The sidebar overlay `<div>` is always rendered in the DOM, even on desktop where it is hidden via CSS (`display: none` in the CSS at line 822). This is not a bug, but it adds an unnecessary DOM node and click handler on desktop. The `onClick` handler is always active, though it won't fire because the div is not displayed.

**Impact:** Minimal. Unnecessary DOM element.

**Suggested Fix:**
Conditionally render the overlay only when `sidebarOpen` is true, or accept this as a reasonable trade-off for simplicity.

---

### LOW-09: No `aria-label` on hamburger button

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 329-331

**Description:**
The hamburger button uses `&#9776;` (a Unicode character) as its content. Screen readers will read this as the Unicode name, not as "Toggle sidebar" or "Menu". There is no `aria-label` attribute.

**Impact:** Screen reader users cannot understand the button's purpose.

**Suggested Fix:**
Add `aria-label="Toggle sidebar menu"` to the button.

---

### LOW-10: `getFileIcon` returns emoji characters which may render differently across platforms

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/FileBadge.jsx`
**Lines:** 43-75

**Description:**
File type icons are emoji characters. Emoji rendering varies significantly across operating systems, browsers, and versions. The visual appearance will be inconsistent between Windows, macOS, Linux, and mobile platforms.

**Impact:** Inconsistent visual appearance across platforms.

**Suggested Fix:**
Consider using SVG icons for consistent cross-platform rendering. Acceptable for V1 MVP.

---

### LOW-11: `formatFileSize` does not handle non-numeric input gracefully

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/components/FileBadge.jsx`
**Lines:** 80-94

**Description:**
If `sizeBytes` is a string (e.g., `"15234"` from JSON), the function works because JavaScript coerces strings to numbers in arithmetic operations. However, if `sizeBytes` is `NaN`, `Infinity`, or some other unexpected value, the function will produce incorrect output. The `bytes == null` check (line 81) handles `null` and `undefined`, and `bytes < 0` handles negative, but `NaN`, strings, and objects are not handled.

**Impact:** Incorrect file size display if server sends unexpected data type.

**Suggested Fix:**
Add `typeof bytes !== 'number'` or `Number.isFinite(bytes)` check.

---

## SSE HANDLING (useSSE.js) -- Specific Review

### SSE-01: No reconnection handling

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/hooks/useSSE.js`

**Description:**
The hook has no automatic retry or reconnection logic. If the SSE stream drops due to a network hiccup or proxy timeout, the stream simply ends. The `finally` block sets `isStreaming` to `false`, and the user sees an incomplete response with no way to retry.

Native `EventSource` has built-in reconnection, but since this uses `fetch` with `ReadableStream` (necessary for POST), reconnection must be implemented manually. The PRD does not specify reconnection behavior, but for a good user experience, at least a "Retry" button should appear.

**Impact:** Stream failures are silent and unrecoverable without resending the message.

---

### SSE-02: Buffer handling does not account for `\r\n` line endings

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/hooks/useSSE.js`
**Lines:** 78, 128

**Description:**
The SSE spec allows both `\n`, `\r\n`, and `\r` as line endings. The parser splits on `\n\n` (line 78) for event boundaries and `\n` (line 128) for lines within an event. If the server sends `\r\n` line endings (common on Windows-based servers or certain HTTP stacks), the parser will not correctly identify event boundaries. The `\r` characters will remain in the parsed data, potentially corrupting JSON parsing.

**Impact:** SSE parsing failure on servers that use `\r\n` line endings.

**Suggested Fix:**
Normalize line endings before parsing:
```js
buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
```

---

### SSE-03: `TextDecoder` with `stream: true` may split multi-byte characters

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/hooks/useSSE.js`
**Line:** 74

**Description:**
The code correctly uses `{ stream: true }` with `TextDecoder`, which handles multi-byte character splitting. However, the final chunk processing (lines 67-71) does NOT use `decoder.decode()` with `{ stream: false }` to flush the decoder's internal buffer. The `if (buffer.trim())` check directly processes the remaining buffer, but if the last chunk split a multi-byte character, the final character may have been buffered in the decoder but not yet flushed to `buffer`.

**Impact:** Potential loss of the last few characters in a response if they are multi-byte (e.g., Unicode characters, emoji).

**Suggested Fix:**
After the read loop exits, flush the decoder:
```js
if (done) {
  buffer += decoder.decode(); // Flush remaining
  if (buffer.trim()) {
    processSSEChunk(buffer, callbacksRef.current);
  }
  break;
}
```

---

## STATE MANAGEMENT (App.jsx) -- Specific Review

### STATE-01: All sessions re-rendered on every text delta

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 54-70

**Description:**
Every `onTextDelta` event triggers `setSessions(prev => prev.map(...))`, which creates a new array and new session objects for ALL sessions, even though only one session changed. This means:
1. The Sidebar receives a new `sessions` array reference on every delta
2. Despite being `memo`-ized, Sidebar re-renders because `sessions` is a new array
3. Every session item in the sidebar re-renders

At 20-30 text deltas per second for a streaming response, this is a significant amount of unnecessary rendering.

**Impact:** Performance degradation, especially with many sessions in the sidebar.

**Suggested Fix:**
Either use a more granular state structure (e.g., separate state for each session's messages), or use `React.useMemo` / selector pattern to prevent Sidebar from re-rendering when only message content changes.

---

### STATE-02: No limit on message array size

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`

**Description:**
There is no limit on the number of messages in a session's message array. For a long conversation with many tool calls, file generations, and thinking blocks, the message array could grow very large. Each message object is cloned on every state update (due to immutable update patterns), and the ChatArea iterates over all messages on every render.

The PRD mentions a 150K token warning but has no client-side message limit.

**Impact:** Progressive memory growth and rendering slowdown for very long conversations.

**Suggested Fix:**
Consider implementing virtual scrolling for long message lists, or warn the user when the conversation gets very long.

---

## API INTEGRATION (api.js) -- Specific Review

### API-01: `createSession` sends empty JSON body with Content-Type header

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/services/api.js`
**Lines:** 38-44

**Description:**
`createSession` sends `body: JSON.stringify({})` with `Content-Type: application/json`. This is technically correct per the PRD (which shows an empty `{}` request body), but some servers may reject or warn on empty JSON bodies. This is a minor concern -- the implementation matches the spec.

**Impact:** None expected, but noted for completeness.

---

### API-02: `handleResponse` assumes response body is always JSON on success

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/services/api.js`
**Line:** 30

**Description:**
On success (`response.ok`), `handleResponse` calls `response.json()` unconditionally (line 30). If the server returns a success response with a non-JSON body (e.g., `204 No Content` or plain text), this will throw an unhandled JSON parse error. The DELETE endpoint, for example, returns JSON per the PRD, but a misconfigured server could return an empty body.

**Impact:** Unhandled exception on non-JSON success responses.

**Suggested Fix:**
Check `Content-Type` header or handle JSON parse errors:
```js
if (response.status === 204) return null;
return response.json();
```

---

### API-03: `getSessionInfo` is defined but never used

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/services/api.js`
**Lines:** 66-71

**Description:**
The `getSessionInfo` function is exported but never imported or called anywhere in the application. This is dead code.

**Impact:** No functional impact. Code maintenance concern.

**Suggested Fix:**
Either remove the function or use it to implement session reconnection on page refresh.

---

## PRD COMPLIANCE GAPS

### PRD-01: Missing `message_start` event handling does not use returned data

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 31-48

**Description:**
The PRD specifies that `message_start` carries `message_id` and `session_id` fields. The `onMessageStart` callback ignores these fields entirely -- it doesn't extract or store `message_id`, nor does it verify that `session_id` matches the active session. This means:
1. There is no message-level identification for correlating subsequent events
2. Events from a wrong session (see CRIT-04) cannot be detected and rejected

**Suggested Fix:**
Store the `message_id` from `message_start` and use it for message-level state tracking. Verify `session_id` matches the active session.

---

### PRD-02: `message_end` usage data is not displayed or stored

**File:** `/Users/peter_parker/Desktop/Sealine-Database/client/src/App.jsx`
**Lines:** 209-227

**Description:**
The PRD specifies that `message_end` carries `{ message_id, usage }` with token counts and SQL call counts. The `onMessageEnd` handler ignores this data entirely, only clearing the `isStreaming` flag. Token usage information is not displayed to the user and not stored for the session.

The PRD mentions (Section 9.4) that the server should warn when token count reaches ~150K. If the client doesn't track or display usage, the user has no visibility into this.

**Suggested Fix:**
Store usage data and display it in the UI (e.g., in the session sidebar or as a footer in the chat area).

---

## Conclusion

The frontend implementation is well-structured overall and follows React best practices for the most part. The most significant issues center around:

1. **Session switching during streaming** (CRIT-04) -- This is the highest-priority fix. Switching sessions while a stream is active will corrupt the target session.
2. **Message targeting in SSE callbacks** (CRIT-03) -- The `onTextDelta` handler should filter by `isStreaming: true` to avoid appending to completed messages.
3. **Array index keys** (CRIT-05) -- Using stable message IDs instead of array indices prevents state corruption in collapsible components.
4. **Performance during streaming** (MOD-08, STATE-01) -- The O(n^2) markdown re-rendering and unnecessary sidebar re-renders will cause noticeable sluggishness on longer conversations.
5. **SSE robustness** (MOD-02, SSE-01, SSE-02, SSE-03) -- The SSE parser needs hardening for edge cases around line endings, cleanup on unmount, and reconnection.

These issues should be prioritized for fixing before the V1 release to ensure a stable and reliable user experience for the target 1-5 concurrent users.
