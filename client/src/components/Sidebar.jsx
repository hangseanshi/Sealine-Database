import { memo } from 'react';

/**
 * Sidebar component.
 * Lists all chat sessions with a "New Chat" button at the top.
 * Each session shows a truncated title and a delete button.
 *
 * Props:
 *  - sessions: array of { id, title, createdAt }
 *  - activeSessionId: currently selected session id
 *  - onSelectSession(id): callback when a session is clicked
 *  - onNewChat(): callback when New Chat is clicked
 *  - onDeleteSession(id): callback when delete button is clicked
 *  - isOpen: boolean for mobile sidebar visibility
 */
function Sidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  onDeleteSession,
  isOpen,
}) {
  return (
    <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
      <div className="sidebar-header">
        <button className="new-chat-btn" onClick={onNewChat}>
          + New Chat
        </button>
      </div>

      <div className="session-list">
        {sessions.map((session) => (
          <div
            key={session.id}
            className={`session-item ${
              session.id === activeSessionId ? 'active' : ''
            }`}
            onClick={() => onSelectSession(session.id)}
          >
            <span className="session-item-icon">💬</span>
            <span className="session-item-title">{session.title}</span>
            <button
              className="session-delete-btn"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteSession(session.id);
              }}
              title="Delete chat"
            >
              ✕
            </button>
          </div>
        ))}

        {sessions.length === 0 && (
          <div
            style={{
              padding: '20px 12px',
              textAlign: 'center',
              color: 'var(--text-sidebar)',
              fontSize: '13px',
              opacity: 0.6,
            }}
          >
            No chats yet. Click "New Chat" to start.
          </div>
        )}
      </div>
    </aside>
  );
}

export default memo(Sidebar);
