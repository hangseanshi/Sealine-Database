import { useState, memo } from 'react';

/**
 * SqlBlock component.
 * Renders a collapsible SQL query execution block.
 *
 * Props:
 *  - query: SQL query string
 *  - result: query result text (null while running)
 *  - isRunning: boolean, whether the query is still executing
 *  - truncated: boolean, whether result was truncated
 */
function SqlBlock({ query, result, isRunning, truncated }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="sql-block">
      <button
        className="sql-block-header"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="sql-icon">🔧</span>
        <span className="sql-label">
          SQL Query
          {isRunning && (
            <span className="sql-running">
              <span className="spinner-small" />
              Running...
            </span>
          )}
          {!isRunning && result !== null && (
            <span
              style={{
                marginLeft: 8,
                fontSize: 12,
                color: 'var(--success-green)',
              }}
            >
              ✓ Complete
            </span>
          )}
        </span>
        <span className={`sql-toggle ${expanded ? 'expanded' : ''}`}>▶</span>
      </button>

      {expanded && (
        <div className="sql-block-body">
          <pre>
            <code>{query}</code>
          </pre>

          {result !== null && (
            <>
              <div className="sql-result-label">Result</div>
              <div className="sql-result-text">
                {result}
                {truncated && (
                  <div
                    style={{
                      marginTop: 8,
                      fontStyle: 'italic',
                      color: 'var(--text-muted)',
                    }}
                  >
                    (Results truncated to 500 rows)
                  </div>
                )}
              </div>
            </>
          )}

          {isRunning && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '8px 0',
                fontSize: 13,
                color: 'var(--text-muted)',
              }}
            >
              <span className="spinner-small" />
              Executing query...
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(SqlBlock);
