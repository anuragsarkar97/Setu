import React from 'react'

function timeAgo(iso) {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60)       return `${Math.floor(diff)}s ago`
  if (diff < 3600)     return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400)    return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export default function SidePanel({
  items, mode, total, loading, error,
  selectedId, onSelect, onClear,
}) {
  return (
    <aside className="panel" data-testid="side-panel">
      <div className="panel__header">
        <div className="panel__title">
          {mode === 'search' ? 'Results' : 'Recent intents'}
        </div>
        <div className="panel__meta">
          {loading ? 'loading…' : `${items.length}${mode === 'search' ? '' : ` / ${total}`}`}
          {mode === 'search' && (
            <button className="panel__clear" onClick={onClear} data-testid="clear-search-btn">
              clear
            </button>
          )}
        </div>
      </div>

      {error && <div className="panel__error">{error}</div>}

      <ul className="panel__list">
        {items.map((it) => (
          <li
            key={it.intent_id}
            className={'card' + (it.intent_id === selectedId ? ' card--selected' : '')}
            onClick={() => onSelect(it.intent_id)}
            data-testid={`intent-card-${it.intent_id}`}
          >
            <div className="card__top">
              <span className="card__type">{it.intent_type || 'other'}</span>
              {it.score != null && (
                <span className="card__score">{(it.score * 100).toFixed(0)}%</span>
              )}
            </div>
            <div className="card__text">{it.summary || it.text}</div>
            <div className="card__bottom">
              {it.location && <span className="card__loc">📍 {it.location}</span>}
              <span className="card__time">{timeAgo(it.created_at)}</span>
            </div>
            {it.tags?.length > 0 && (
              <div className="card__tags">
                {it.tags.slice(0, 4).map((t, idx) => (
                  <span key={idx} className="tag">{t}</span>
                ))}
              </div>
            )}
          </li>
        ))}
        {!loading && items.length === 0 && (
          <li className="panel__empty">No intents to show.</li>
        )}
      </ul>
    </aside>
  )
}
