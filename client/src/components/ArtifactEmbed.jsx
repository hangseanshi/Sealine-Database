import { useState, memo } from 'react';
import CopyButton from './CopyButton';

/**
 * ArtifactEmbed — unified artifact display component.
 *
 * Rendering rules by file type:
 *   image/*          → <img> inline (click to open full size)
 *   text/html        → <iframe> embedded in chat (collapsible, default expanded)
 *   application/pdf  → <iframe> embedded in chat (collapsible, default expanded)
 *   spreadsheet/csv  → download badge (not embeddable in browser)
 *
 * Props:
 *  - fileId:      unique file identifier
 *  - filename:    display name  (e.g. "report.html")
 *  - fileType:    MIME type string
 *  - url:         URL to the file  (/api/files/<file_id>)
 *  - downloadUrl: same as url, kept for compatibility
 *  - sizeBytes:   file size in bytes (shown on download badge)
 */
function ArtifactEmbed({ fileId, filename, fileType, url, downloadUrl, sizeBytes }) {
  const [expanded, setExpanded] = useState(true);

  const src = url || downloadUrl || (fileId ? `/api/files/${fileId}` : '');
  const ext = filename ? filename.split('.').pop().toLowerCase() : '';

  // ── Detect type ────────────────────────────────────────────────────────────
  const isImage =
    fileType?.startsWith('image/') ||
    ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext);

  const isHtml =
    fileType?.includes('html') || ext === 'html';

  const isPdf =
    fileType?.includes('pdf') || ext === 'pdf';

  const isExcel =
    fileType?.includes('spreadsheet') ||
    fileType?.includes('excel') ||
    ['xlsx', 'xls'].includes(ext);

  const isCsv = ext === 'csv' || fileType?.includes('csv');

  // ── PNG / image ────────────────────────────────────────────────────────────
  if (isImage) {
    return (
      <div className="artifact-embed artifact-image">
        <img
          src={src}
          alt={filename || 'Chart'}
          title="Click to open full size"
          onClick={() => window.open(src, '_blank', 'noopener,noreferrer')}
          loading="lazy"
        />
        {filename && <div className="artifact-caption">{filename}</div>}
      </div>
    );
  }

  // ── HTML / PDF — iFrame ────────────────────────────────────────────────────
  if (isHtml || isPdf) {
    const icon = isPdf ? '📄' : '🌐';
    const iframeHeight = isPdf ? 640 : 520;

    return (
      <div className="artifact-embed artifact-iframe-wrapper">
        <div className="artifact-header">
          <span className="artifact-icon">{icon}</span>
          <span className="artifact-name" title={filename}>{filename}</span>
          <div className="artifact-actions">
            <CopyButton
              getText={() => window.location.origin + src}
              title="Copy link"
            />
            <button
              className="artifact-toggle-btn"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? '▲ Collapse' : '▼ Expand'}
            </button>
            <a
              href={src}
              target="_blank"
              rel="noopener noreferrer"
              className="artifact-newtab-btn"
            >
              ↗ New tab
            </a>
          </div>
        </div>

        {expanded && (
          <iframe
            src={src}
            title={filename || 'artifact'}
            className="artifact-iframe"
            style={{ height: iframeHeight }}
            frameBorder="0"
            loading="lazy"
            allowFullScreen
          />
        )}
      </div>
    );
  }

  // ── Excel / CSV — download badge ───────────────────────────────────────────
  const icon = isExcel ? '📊' : isCsv ? '📋' : '📎';
  const size = formatFileSize(sizeBytes);

  return (
    <div className="file-badge">
      <div className="file-badge-icon">{icon}</div>
      <div className="file-badge-info">
        <div className="file-badge-name" title={filename}>{filename}</div>
        {size && <div className="file-badge-size">{size}</div>}
      </div>
      <a
        className="file-download-btn"
        href={src}
        download={filename}
        target="_blank"
        rel="noopener noreferrer"
      >
        ↓ Download
      </a>
    </div>
  );
}

function formatFileSize(bytes) {
  if (bytes == null || bytes < 0) return '';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let unitIndex = 0;
  let size = bytes;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex++;
  }
  return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

export default memo(ArtifactEmbed);
