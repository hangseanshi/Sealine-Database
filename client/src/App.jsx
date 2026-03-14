import { useState, useCallback, useRef, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatArea from './components/ChatArea';
import InputBar from './components/InputBar';
import useSSE from './hooks/useSSE';
import { createSession, deleteSession } from './services/api';

// Stable incrementing counter for unique message IDs
let nextId = 0;
const getNextId = () => ++nextId;

/**
 * Main App component.
 *
 * State:
 *  - sessions: array of { id, createdAt, title, messages[] }
 *  - activeSessionId: currently viewed session
 *  - sidebarOpen: whether mobile sidebar is visible
 *
 * Each message in a session's messages array has a shape:
 *  { type: 'user' | 'agent' | 'sql' | 'file' | 'plot' | 'error' | 'thinking', ... }
 */
export default function App() {
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Ref to track the current active session for callbacks
  // (avoids stale closure issues in SSE callbacks)
  const activeSessionRef = useRef(null);
  activeSessionRef.current = activeSessionId;

  // --- SSE Callbacks ---
  const sseCallbacks = {
    onMessageStart: useCallback(() => {
      // Add a new empty agent message to the active session
      const sessionId = activeSessionRef.current;
      if (!sessionId) return;

      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          return {
            ...s,
            messages: [
              ...s.messages,
              { id: getNextId(), type: 'agent', text: '', isStreaming: true },
            ],
          };
        })
      );
    }, []),

    onTextDelta: useCallback(({ delta }) => {
      const sessionId = activeSessionRef.current;
      if (!sessionId) return;

      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          const messages = [...s.messages];

          // Find the last streaming agent message
          let lastAgentIdx = -1;
          for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].type === 'agent' && messages[i].isStreaming) {
              lastAgentIdx = i;
              break;
            }
          }

          // If the last streaming agent bubble is already the tail message,
          // just append. Otherwise (a SQL block or artifact was added after it),
          // start a new agent bubble so text appears sequentially after the tool.
          if (lastAgentIdx !== -1 && lastAgentIdx === messages.length - 1) {
            messages[lastAgentIdx] = {
              ...messages[lastAgentIdx],
              text: messages[lastAgentIdx].text + delta,
            };
          } else {
            messages.push({ id: getNextId(), type: 'agent', text: delta, isStreaming: true });
          }

          return { ...s, messages };
        })
      );
    }, []),

    onThinking: useCallback(({ content }) => {
      const sessionId = activeSessionRef.current;
      if (!sessionId) return;

      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          // Check if the last message is a thinking block; if so, append
          const messages = [...s.messages];
          const lastMsg = messages[messages.length - 1];
          if (lastMsg && lastMsg.type === 'thinking') {
            messages[messages.length - 1] = {
              ...lastMsg,
              content: lastMsg.content + content,
            };
          } else {
            messages.push({ id: getNextId(), type: 'thinking', content });
          }
          return { ...s, messages };
        })
      );
    }, []),

    onToolStart: useCallback(({ tool, query }) => {
      // Only show the SQL block for execute_sql — file-generation tools
      // (generate_plot, generate_excel, generate_pdf) emit file/plot events
      // instead of tool_result, so they would get stuck as "Running...".
      if (tool !== 'execute_sql') return;

      const sessionId = activeSessionRef.current;
      if (!sessionId) return;

      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          return {
            ...s,
            messages: [
              ...s.messages,
              { id: getNextId(), type: 'sql', tool, query, result: null, isRunning: true },
            ],
          };
        })
      );
    }, []),

    onToolResult: useCallback(({ tool, result, truncated }) => {
      const sessionId = activeSessionRef.current;
      if (!sessionId) return;

      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          const messages = [...s.messages];
          // Find the last SQL block that is still running
          for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].type === 'sql' && messages[i].isRunning) {
              messages[i] = {
                ...messages[i],
                result,
                truncated,
                isRunning: false,
              };
              break;
            }
          }
          return { ...s, messages };
        })
      );
    }, []),

    onFileGenerated: useCallback(
      ({ file_id, filename, type, download_url, size_bytes }) => {
        const sessionId = activeSessionRef.current;
        if (!sessionId) return;

        setSessions((prev) =>
          prev.map((s) => {
            if (s.id !== sessionId) return s;
            return {
              ...s,
              messages: [
                ...s.messages,
                {
                  id: getNextId(),
                  type: 'file',
                  fileId: file_id,
                  filename,
                  fileType: type,
                  downloadUrl: download_url,
                  sizeBytes: size_bytes,
                },
              ],
            };
          })
        );
      },
      []
    ),

    onPlotGenerated: useCallback(({ file_id, filename, type, url }) => {
      const sessionId = activeSessionRef.current;
      if (!sessionId) return;

      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          return {
            ...s,
            messages: [
              ...s.messages,
              {
                id: getNextId(),
                type: 'plot',
                fileId: file_id,
                filename,
                fileType: type,
                url,
              },
            ],
          };
        })
      );
    }, []),

    onError: useCallback(({ error, code, recoverable }) => {
      const sessionId = activeSessionRef.current;
      if (!sessionId) return;

      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          return {
            ...s,
            messages: [
              ...s.messages,
              { id: getNextId(), type: 'error', error, code, recoverable },
            ],
          };
        })
      );
    }, []),

    onMessageEnd: useCallback(() => {
      const sessionId = activeSessionRef.current;
      if (!sessionId) return;

      // Mark ALL streaming agent messages as done, and remove any that ended
      // up empty (e.g. the initial placeholder if the agent started with a tool).
      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          const messages = s.messages
            .map((msg) =>
              msg.type === 'agent' && msg.isStreaming
                ? { ...msg, isStreaming: false }
                : msg
            )
            .filter((msg) => !(msg.type === 'agent' && !msg.text));
          return { ...s, messages };
        })
      );
    }, []),
  };

  const { sendMessage, isStreaming, abort } = useSSE(sseCallbacks);

  // --- Session Actions ---

  // Derive a friendly first name from the OS login username.
  // Handles: "sean", "sean.smith", "sean_smith", "Sean Smith", "DOMAIN\sean"
  const getFirstName = (username) => {
    if (!username) return null;
    const stripped = username.includes('\\') ? username.split('\\').pop() : username;
    const first = stripped.split(/[\s._-]/)[0];
    return first ? first.charAt(0).toUpperCase() + first.slice(1).toLowerCase() : null;
  };

  const handleNewChat = useCallback(async () => {
    try {
      const data = await createSession();

      const firstName = getFirstName(data.username);
      const greeting = firstName
        ? `Hi ${firstName}, welcome to your Sealine data chat.`
        : 'Welcome to your Sealine data chat.';

      const newSession = {
        id: data.session_id,
        createdAt: data.created_at,
        title: 'New Chat',
        messages: [
          { id: getNextId(), type: 'agent', text: greeting, isStreaming: false },
        ],
      };
      setSessions((prev) => [newSession, ...prev]);
      setActiveSessionId(data.session_id);
      setSidebarOpen(false);
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  }, []);

  const handleDeleteSession = useCallback(
    async (sessionId) => {
      try {
        await deleteSession(sessionId);
      } catch {
        // Server might already have cleaned up; proceed with client removal
      }
      setSessions((prev) => {
        const remaining = prev.filter((s) => s.id !== sessionId);
        if (activeSessionId === sessionId) {
          setActiveSessionId(remaining.length > 0 ? remaining[0].id : null);
        }
        return remaining;
      });
    },
    [activeSessionId]
  );

  const handleSelectSession = useCallback((sessionId) => {
    abort();
    setActiveSessionId(sessionId);
    setSidebarOpen(false);
  }, [abort]);

  const handleSendMessage = useCallback(
    async (text) => {
      if (!activeSessionId || !text.trim()) return;

      // Add user message to the session
      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== activeSessionId) return s;
          const updatedSession = {
            ...s,
            messages: [...s.messages, { id: getNextId(), type: 'user', text: text.trim() }],
          };
          // Update session title to the first user message
          if (s.title === 'New Chat') {
            updatedSession.title =
              text.trim().length > 50
                ? text.trim().substring(0, 50) + '...'
                : text.trim();
          }
          return updatedSession;
        })
      );

      // Trigger the SSE stream
      await sendMessage(activeSessionId, text.trim());
    },
    [activeSessionId, sendMessage]
  );

  const toggleSidebar = useCallback(() => {
    setSidebarOpen((prev) => !prev);
  }, []);

  // Auto-create a new chat session when the app first loads
  useEffect(() => {
    handleNewChat();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Find the active session
  const activeSession = sessions.find((s) => s.id === activeSessionId) || null;

  return (
    <div className="app-container">
      {/* Mobile overlay */}
      <div
        className={`sidebar-overlay ${sidebarOpen ? 'open' : ''}`}
        onClick={() => setSidebarOpen(false)}
      />

      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        onDeleteSession={handleDeleteSession}
        isOpen={sidebarOpen}
      />

      <div className="main-content">
        <header className="header-bar">
          <button className="hamburger-btn" onClick={toggleSidebar}>
            &#9776;
          </button>
          <h1>Sealine Data Chat</h1>
          <div style={{ width: 32 }} /> {/* Spacer for centering */}
        </header>

        <ChatArea
          session={activeSession}
          isStreaming={isStreaming}
        />

        <InputBar
          onSend={handleSendMessage}
          isLoading={isStreaming}
          disabled={!activeSessionId}
        />
      </div>
    </div>
  );
}
