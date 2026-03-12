import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkBreaks from 'remark-breaks';
import CopyButton from './CopyButton';

/**
 * MessageBubble component.
 * Renders a single chat message (user or agent).
 *
 * Props:
 *  - role: 'user' | 'agent'
 *  - text: message text content
 *  - isStreaming: boolean, whether the agent message is still streaming
 */
function MessageBubble({ role, text, isStreaming }) {
  return (
    <div className={`message-row ${role}`}>
      <div className={`message-bubble ${role}`}>
        {role === 'user' ? (
          // User messages are rendered as plain text
          <div style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
        ) : (
          // Agent messages are rendered as Markdown
          <>
            {text ? (
              <ReactMarkdown remarkPlugins={[remarkBreaks]} rehypePlugins={[rehypeHighlight]}>
                {text}
              </ReactMarkdown>
            ) : isStreaming ? (
              <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                Thinking...
              </span>
            ) : null}
            {isStreaming && text && <span className="streaming-cursor" />}
          </>
        )}

        {/* Copy button — fades in on bubble hover via CSS */}
        {text && !isStreaming && (
          <CopyButton getText={text} className="message-copy-btn" title="Copy message" />
        )}
      </div>
    </div>
  );
}

export default memo(MessageBubble);
