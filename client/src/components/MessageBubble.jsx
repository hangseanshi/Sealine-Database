import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeRaw from 'rehype-raw';
import remarkBreaks from 'remark-breaks';
import CopyButton from './CopyButton';

/**
 * Strip fenced code blocks (``` ... ```) from agent message text.
 * SQL queries are already shown via the separate SqlBlock component,
 * so we don't want them duplicated inside the message bubble.
 */
function stripCodeBlocks(text) {
  return text
    // Remove fenced code blocks with optional language tag: ```lang\n...\n```
    .replace(/```[\w]*\n[\s\S]*?```/g, '')
    // Collapse runs of 3+ blank lines left behind into a single blank line
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

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
  const displayText = role === 'agent' && text ? stripCodeBlocks(text) : text;

  return (
    <div className={`message-row ${role}`}>
      <div className={`message-bubble ${role}`}>
        {role === 'user' ? (
          // User messages are rendered as plain text
          <div style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
        ) : (
          // Agent messages are rendered as Markdown (code blocks stripped — shown via SqlBlock)
          <>
            {displayText ? (
              <ReactMarkdown remarkPlugins={[remarkBreaks]} rehypePlugins={[rehypeRaw, rehypeHighlight]}>
                {displayText}
              </ReactMarkdown>
            ) : isStreaming ? (
              <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                Thinking...
              </span>
            ) : null}
            {isStreaming && displayText && <span className="streaming-cursor" />}
          </>
        )}

        {/* Copy button — copies original full text including any code blocks */}
        {text && !isStreaming && (
          <CopyButton getText={text} className="message-copy-btn" title="Copy message" />
        )}
      </div>
    </div>
  );
}

export default memo(MessageBubble);
