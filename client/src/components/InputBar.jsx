import { useState, useRef, useCallback, useEffect, memo } from 'react';
import CopyButton from './CopyButton';

/**
 * InputBar component.
 * Text input area with Send button.
 * Enter sends the message, Shift+Enter inserts a newline.
 * Auto-grows the textarea as content increases.
 *
 * Props:
 *  - onSend(text): callback when user sends a message
 *  - isLoading: boolean, whether a response is streaming
 *  - disabled: boolean, whether input should be fully disabled (no active session)
 */
function InputBar({ onSend, isLoading, disabled }) {
  const [text, setText] = useState('');
  const textareaRef = useRef(null);

  // Auto-resize the textarea based on content
  const adjustHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 140) + 'px';
  }, []);

  useEffect(() => {
    adjustHeight();
  }, [text, adjustHeight]);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || isLoading || disabled) return;
    onSend(trimmed);
    setText('');
    // Reset textarea height after clearing
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [text, isLoading, disabled, onSend]);

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const handleChange = useCallback((e) => {
    setText(e.target.value);
  }, []);

  const isDisabled = disabled || isLoading;

  return (
    <div className="input-bar">
      <textarea
        ref={textareaRef}
        value={text}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={
          disabled
            ? 'Start a new chat to begin...'
            : 'Ask a question about the shipping data...'
        }
        disabled={isDisabled}
        rows={1}
      />
      {text && (
        <CopyButton
          getText={text}
          className="input-copy-btn"
          title="Copy input text"
        />
      )}
      <button
        className="send-btn"
        onClick={handleSend}
        disabled={isDisabled || !text.trim()}
        title="Send message"
      >
        {isLoading ? (
          <div className="spinner" />
        ) : (
          <span className="send-icon">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </span>
        )}
      </button>
    </div>
  );
}

export default memo(InputBar);
