import { memo } from 'react';

/**
 * InlinePlot component.
 * Renders an inline chart/plot image within the chat.
 * Clickable to open the full-size image in a new tab.
 *
 * Props:
 *  - fileId: unique identifier for the file
 *  - filename: display name (used as alt text)
 *  - url: URL to the image (e.g. /api/files/{file_id})
 */
function InlinePlot({ fileId, filename, url }) {
  const handleClick = () => {
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  return (
    <div className="inline-plot">
      <img
        src={url}
        alt={filename || 'Chart'}
        title="Click to open full size"
        onClick={handleClick}
        loading="lazy"
      />
      {filename && <div className="inline-plot-caption">{filename}</div>}
    </div>
  );
}

export default memo(InlinePlot);
