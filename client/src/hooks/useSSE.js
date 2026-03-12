import { useState, useRef, useCallback, useEffect } from 'react';

/**
 * Custom hook for SSE streaming via POST requests.
 *
 * Uses fetch() with ReadableStream since native EventSource only supports GET.
 * Parses the SSE wire format: events separated by \n\n, with event: and data: lines.
 *
 * @param {Object} callbacks - Event handler callbacks
 * @param {Function} callbacks.onMessageStart - Called with {message_id, session_id}
 * @param {Function} callbacks.onTextDelta - Called with {delta}
 * @param {Function} callbacks.onThinking - Called with {content}
 * @param {Function} callbacks.onToolStart - Called with {tool, query}
 * @param {Function} callbacks.onToolResult - Called with {tool, result, truncated}
 * @param {Function} callbacks.onFileGenerated - Called with {file_id, filename, type, download_url, size_bytes}
 * @param {Function} callbacks.onPlotGenerated - Called with {file_id, filename, type, url}
 * @param {Function} callbacks.onError - Called with {error, code, recoverable}
 * @param {Function} callbacks.onMessageEnd - Called with {message_id, usage}
 * @returns {{ sendMessage: Function, isStreaming: boolean, error: string|null }}
 */
export default function useSSE(callbacks) {
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState(null);
  const abortControllerRef = useRef(null);
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const sendMessage = useCallback(async (sessionId, message) => {
    // Abort any existing stream
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    setIsStreaming(true);
    setError(null);

    try {
      const response = await fetch(`/api/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
        signal: abortController.signal,
      });

      if (!response.ok) {
        let errorMsg = `Server error: ${response.status}`;
        try {
          const errorBody = await response.json();
          errorMsg = errorBody.error || errorMsg;
        } catch {
          // Non-JSON error response
        }
        throw new Error(errorMsg);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          // Process any remaining data in buffer
          if (buffer.trim()) {
            processSSEChunk(buffer, callbacksRef.current);
          }
          break;
        }

        buffer += decoder.decode(value, { stream: true });

        // SSE events are separated by double newlines
        // Process all complete events in the buffer
        const events = buffer.split('\n\n');

        // The last element may be incomplete, so keep it in the buffer
        buffer = events.pop() || '';

        for (const eventChunk of events) {
          if (eventChunk.trim()) {
            processSSEChunk(eventChunk, callbacksRef.current);
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        // Stream was intentionally aborted
        return;
      }
      const errorMessage = err.message || 'Stream connection failed';
      setError(errorMessage);
      if (callbacksRef.current.onError) {
        callbacksRef.current.onError({
          error: errorMessage,
          code: 'STREAM_ERROR',
          recoverable: true,
        });
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  }, []);

  const abort = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  // Abort the active stream on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
    };
  }, []);

  return { sendMessage, isStreaming, error, abort };
}

/**
 * Parse a single SSE event chunk into event type and data,
 * then dispatch to the appropriate callback.
 *
 * An SSE chunk looks like:
 *   event: text_delta
 *   data: {"delta": "Hello"}
 *
 * Or just:
 *   data: {"delta": "Hello"}
 *
 * May also have multi-line data:
 *   data: {"delta":
 *   data: "Hello"}
 */
function processSSEChunk(chunk, callbacks) {
  const lines = chunk.split('\n');
  let eventType = 'message'; // default SSE event type
  let dataLines = [];

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    } else if (line.startsWith(':')) {
      // SSE comment line, ignore
      continue;
    }
  }

  if (dataLines.length === 0) {
    return;
  }

  const dataStr = dataLines.join('\n');
  let data;
  try {
    data = JSON.parse(dataStr);
  } catch {
    // If data is not valid JSON, wrap it
    data = { raw: dataStr };
  }

  // Dispatch to the appropriate callback
  switch (eventType) {
    case 'message_start':
      callbacks.onMessageStart?.(data);
      break;
    case 'text_delta':
      callbacks.onTextDelta?.(data);
      break;
    case 'thinking':
      callbacks.onThinking?.(data);
      break;
    case 'tool_start':
      callbacks.onToolStart?.(data);
      break;
    case 'tool_result':
      callbacks.onToolResult?.(data);
      break;
    case 'file_generated':
      callbacks.onFileGenerated?.(data);
      break;
    case 'plot_generated':
      callbacks.onPlotGenerated?.(data);
      break;
    case 'error':
      callbacks.onError?.(data);
      break;
    case 'message_end':
      callbacks.onMessageEnd?.(data);
      break;
    default:
      // Unknown event type, ignore
      break;
  }
}
