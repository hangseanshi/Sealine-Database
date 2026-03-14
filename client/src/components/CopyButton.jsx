import { useState, useCallback } from 'react';

/**
 * CopyButton — small clipboard icon that briefly shows a checkmark on success.
 *
 * Props:
 *  - getText: string | () => string  — content to copy
 *  - className: extra CSS classes (optional)
 *  - title: tooltip override (optional)
 */
export default function CopyButton({ getText, className = '', title }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async (e) => {
    e.stopPropagation(); // don't trigger parent click handlers (e.g. SQL expand)
    try {
      const text = typeof getText === 'function' ? getText() : (getText ?? '');
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Fallback for browsers without clipboard API
      try {
        const ta = document.createElement('textarea');
        ta.value = typeof getText === 'function' ? getText() : (getText ?? '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      } catch { /* give up silently */ }
    }
  }, [getText]);

  return (
    <button
      className={`copy-btn ${copied ? 'copied' : ''} ${className}`}
      onClick={handleCopy}
      title={copied ? 'Copied!' : (title ?? 'Copy to clipboard')}
      aria-label="Copy to clipboard"
    >
      {copied ? (
        // Checkmark
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.5"
          strokeLinecap="round" strokeLinejoin="round">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      ) : (
        // Clipboard icon
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      )}
    </button>
  );
}
