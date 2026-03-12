import { useState, useEffect, useRef } from 'react';
import MessageBubble from './MessageBubble';
import SqlBlock from './SqlBlock';
import FileBadge from './FileBadge';
import InlinePlot from './InlinePlot';

/**
 * ChatArea component.
 * Displays messages for the active session with auto-scrolling.
 * Shows a welcome message when no messages exist.
 *
 * Props:
 *  - session: { id, messages[] } or null
 *  - isStreaming: boolean indicating if currently streaming
 */
export default function ChatArea({ session, isStreaming }) {
  const messagesEndRef = useRef(null);
  const containerRef = useRef(null);

  // Auto-scroll to bottom when new messages arrive or content changes
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [session?.messages]);

  // No session selected
  if (!session) {
    return (
      <div className="chat-area">
        <div className="no-session">
          <div className="no-session-icon">🚢</div>
          <p>Select a chat or start a new one to begin.</p>
        </div>
      </div>
    );
  }

  const { messages } = session;

  // Empty session (no messages yet)
  if (messages.length === 0) {
    return (
      <div className="chat-area">
        <div className="messages-container" ref={containerRef}>
          <div className="welcome-message">
            <div className="welcome-icon">🚢</div>
            <h2>Welcome to Sealine Data Chat</h2>
            <p>
              Ask me anything about the Sealine shipping database. I can query
              shipment data, generate reports, create charts, and help you
              analyze container tracking information.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-area">
      <div className="messages-container" ref={containerRef}>
        {messages.map((msg, idx) => {
          switch (msg.type) {
            case 'user':
              return (
                <MessageBubble key={msg.id ?? idx} role="user" text={msg.text} />
              );

            case 'agent':
              return (
                <MessageBubble
                  key={msg.id ?? idx}
                  role="agent"
                  text={msg.text}
                  isStreaming={msg.isStreaming}
                />
              );

            case 'sql':
              return (
                <SqlBlock
                  key={msg.id ?? idx}
                  query={msg.query}
                  result={msg.result}
                  isRunning={msg.isRunning}
                  truncated={msg.truncated}
                />
              );

            case 'file':
              return (
                <FileBadge
                  key={msg.id ?? idx}
                  fileId={msg.fileId}
                  filename={msg.filename}
                  fileType={msg.fileType}
                  downloadUrl={msg.downloadUrl}
                  sizeBytes={msg.sizeBytes}
                />
              );

            case 'plot':
              return (
                <InlinePlot
                  key={msg.id ?? idx}
                  fileId={msg.fileId}
                  filename={msg.filename}
                  url={msg.url}
                />
              );

            case 'error':
              return (
                <div key={msg.id ?? idx} className="error-message">
                  <div className="error-label">Error</div>
                  <div>{msg.error}</div>
                </div>
              );

            case 'thinking':
              return <ThinkingBlock key={msg.id ?? idx} content={msg.content} />;

            default:
              return null;
          }
        })}
        <div ref={messagesEndRef} />
      </div>
    </div>
  );
}

/**
 * Collapsible thinking block.
 * Shows Claude's reasoning in a dimmed italic block, collapsed by default.
 */
function ThinkingBlock({ content }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="thinking-block">
      <button
        className="thinking-header"
        onClick={() => setExpanded(!expanded)}
      >
        <span className={`thinking-toggle ${expanded ? 'expanded' : ''}`}>
          ▶
        </span>
        <span>💭 Thinking...</span>
      </button>
      {expanded && <div className="thinking-body">{content}</div>}
    </div>
  );
}
