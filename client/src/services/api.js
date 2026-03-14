/**
 * API client for the Sealine Data Chat backend.
 * All functions return parsed JSON and throw consistent error objects.
 */

const API_BASE = '/api';

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function handleResponse(response) {
  if (!response.ok) {
    let errorMessage = `Request failed with status ${response.status}`;
    let errorCode = 'UNKNOWN';
    try {
      const body = await response.json();
      errorMessage = body.error || body.message || errorMessage;
      errorCode = body.code || errorCode;
    } catch {
      // Response body is not JSON; use default message.
    }
    throw new ApiError(errorMessage, response.status, errorCode);
  }
  return response.json();
}

/**
 * Create a new chat session.
 * POST /api/sessions
 * @returns {Promise<{session_id: string, created_at: string, model: string, db_enabled: boolean}>}
 */
export async function createSession() {
  const response = await fetch(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return handleResponse(response);
}

/**
 * Delete a chat session.
 * DELETE /api/sessions/{sessionId}
 * @param {string} sessionId
 * @returns {Promise<{status: string, session_id: string}>}
 */
export async function deleteSession(sessionId) {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}`, {
    method: 'DELETE',
  });
  return handleResponse(response);
}

/**
 * Get session info and usage statistics.
 * GET /api/sessions/{sessionId}
 * @param {string} sessionId
 * @returns {Promise<Object>}
 */
export async function getSessionInfo(sessionId) {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}`, {
    method: 'GET',
  });
  return handleResponse(response);
}
