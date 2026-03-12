import { memo } from 'react';

/**
 * FileBadge component.
 * Renders a downloadable file card with icon, name, size, and download button.
 *
 * Props:
 *  - fileId: unique file identifier
 *  - filename: display name of the file
 *  - fileType: MIME type string
 *  - downloadUrl: URL to download the file (e.g. /api/files/{id})
 *  - sizeBytes: file size in bytes
 */
function FileBadge({ fileId, filename, fileType, downloadUrl, sizeBytes }) {
  const icon = getFileIcon(fileType, filename);
  const size = formatFileSize(sizeBytes);

  return (
    <div className="file-badge">
      <div className="file-badge-icon">{icon}</div>
      <div className="file-badge-info">
        <div className="file-badge-name" title={filename}>
          {filename}
        </div>
        <div className="file-badge-size">{size}</div>
      </div>
      <a
        className="file-download-btn"
        href={downloadUrl}
        download={filename}
        target="_blank"
        rel="noopener noreferrer"
      >
        ↓ Download
      </a>
    </div>
  );
}

/**
 * Returns an appropriate icon based on file type or extension.
 */
function getFileIcon(fileType, filename) {
  if (!fileType && !filename) return '📎';

  const ext = filename ? filename.split('.').pop().toLowerCase() : '';

  if (
    fileType?.includes('spreadsheet') ||
    fileType?.includes('excel') ||
    ext === 'xlsx' ||
    ext === 'xls' ||
    ext === 'csv'
  ) {
    return '📊';
  }

  if (fileType?.includes('pdf') || ext === 'pdf') {
    return '📄';
  }

  if (fileType?.includes('image') || ['png', 'jpg', 'jpeg', 'gif', 'svg'].includes(ext)) {
    return '🖼️';
  }

  if (fileType?.includes('html') || ext === 'html') {
    return '🌐';
  }

  if (fileType?.includes('text') || ext === 'txt') {
    return '📝';
  }

  return '📎';
}

/**
 * Formats bytes into a human-readable string (e.g. "12.5 KB", "1.3 MB").
 */
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

export default memo(FileBadge);
