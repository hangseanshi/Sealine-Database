import { useState, useRef, useCallback, useEffect, memo } from 'react';
import CopyButton from './CopyButton';

/**
 * InputBar component.
 * Text input area with Send button and microphone for voice input.
 * Enter sends the message, Shift+Enter inserts a newline.
 * Auto-grows the textarea as content increases.
 *
 * Props:
 *  - onSend(text): callback when user sends a message
 *  - isLoading: boolean, whether a response is streaming
 *  - disabled: boolean, whether input should be fully disabled (no active session)
 */

const SpeechRecognition =
  typeof window !== 'undefined'
    ? window.SpeechRecognition || window.webkitSpeechRecognition
    : null;

function InputBar({ onSend, isLoading, disabled }) {
  const [text, setText] = useState('');
  const [isListening, setIsListening] = useState(false);
  const textareaRef = useRef(null);
  const recognitionRef = useRef(null);
  const historyRef = useRef([]);      // sent messages, oldest→newest
  const historyIdxRef = useRef(-1);   // -1 = not navigating

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

  // Focus on mount
  useEffect(() => {
    if (textareaRef.current && !disabled) {
      textareaRef.current.focus();
    }
  }, [disabled]);

  // Re-focus after response finishes streaming
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    if (prevLoadingRef.current && !isLoading && !disabled) {
      textareaRef.current?.focus();
    }
    prevLoadingRef.current = isLoading;
  }, [isLoading, disabled]);

  // Cleanup recognition on unmount
  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.abort();
      }
    };
  }, []);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || isLoading || disabled) return;
    // Push to history (avoid duplicate consecutive entries)
    const hist = historyRef.current;
    if (hist[hist.length - 1] !== trimmed) {
      hist.push(trimmed);
    }
    historyIdxRef.current = -1;
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
        return;
      }

      const hist = historyRef.current;
      if (!hist.length) return;

      if (e.key === 'ArrowUp') {
        // Only navigate history when cursor is on the first line
        const textarea = textareaRef.current;
        if (textarea && textarea.selectionStart !== 0 && text.includes('\n')) return;
        e.preventDefault();
        const nextIdx =
          historyIdxRef.current === -1
            ? hist.length - 1
            : Math.max(0, historyIdxRef.current - 1);
        historyIdxRef.current = nextIdx;
        setText(hist[nextIdx]);
      } else if (e.key === 'ArrowDown') {
        if (historyIdxRef.current === -1) return;
        e.preventDefault();
        const nextIdx = historyIdxRef.current + 1;
        if (nextIdx >= hist.length) {
          historyIdxRef.current = -1;
          setText('');
        } else {
          historyIdxRef.current = nextIdx;
          setText(hist[nextIdx]);
        }
      }
    },
    [handleSend, text]
  );

  const handleChange = useCallback((e) => {
    setText(e.target.value);
  }, []);

  const toggleListening = useCallback(() => {
    if (!SpeechRecognition) {
      alert('Speech recognition is not supported in your browser. Please use Chrome or Edge.');
      return;
    }

    if (isListening) {
      // Stop listening
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
      setIsListening(false);
      return;
    }

    // Start listening
    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';
    recognitionRef.current = recognition;

    // Snapshot the text that existed before we started recording
    const baseText = text;

    recognition.onresult = (event) => {
      // Rebuild the full transcript from ALL results every time
      let full = '';
      for (let i = 0; i < event.results.length; i++) {
        full += event.results[i][0].transcript;
      }
      const separator = baseText && !baseText.endsWith(' ') ? ' ' : '';
      setText(baseText + separator + full);
    };

    recognition.onerror = (event) => {
      console.error('Speech recognition error:', event.error);
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognition.start();
    setIsListening(true);
  }, [isListening]);

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
      {SpeechRecognition && (
        <button
          className={`mic-btn${isListening ? ' listening' : ''}`}
          onClick={toggleListening}
          disabled={disabled}
          title={isListening ? 'Stop listening' : 'Voice input'}
          type="button"
        >
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
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
        </button>
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
