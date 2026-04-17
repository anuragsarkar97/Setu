import React, { useEffect, useMemo, useRef, useState } from 'react'
import Globe from './Globe.jsx'
import SidePanel from './SidePanel.jsx'
import { fetchIntents, searchIntents } from './api.js'

export default function App() {
  const [intents, setIntents]         = useState([])   // full list from /api/intents
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState(null)

  const [query, setQuery]             = useState('')
  const [searching, setSearching]     = useState(false)
  const [searchResults, setResults]   = useState(null)  // null = no search, [] = searched but empty

  const [selectedId, setSelectedId]   = useState(null)
  const [flyToId, setFlyToId]         = useState(null)

  // intents keyed by id for fast lookup
  const intentsById = useMemo(() => {
    const m = new Map()
    for (const i of intents) m.set(i.intent_id, i)
    return m
  }, [intents])

  // initial load
  useEffect(() => {
    fetchIntents()
      .then((data) => { setIntents(data); setLoading(false) })
      .catch((e)   => { setError(e.message); setLoading(false) })
  }, [])

  // side-panel items: search results (merged with intent geo) OR full list
  const panelItems = useMemo(() => {
    if (searchResults) {
      return searchResults.map((m) => {
        const full = intentsById.get(m.intent_id) || {}
        return {
          intent_id:   m.intent_id,
          text:        m.text || full.text,
          summary:     full.summary,
          intent_type: m.intent_type || full.intent_type,
          location:    m.location    || full.location,
          tags:        m.tags        || full.tags,
          created_at:  full.created_at,
          score:       m.relevance ?? m.cosine,
        }
      })
    }
    // default: recency desc
    return [...intents].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
  }, [searchResults, intents, intentsById])

  const highlightedIds = useMemo(() => {
    if (!searchResults) return null
    return new Set(searchResults.map((r) => r.intent_id))
  }, [searchResults])

  // pin click / card click — same handler
  const handleSelect = (id) => {
    setSelectedId(id)
    setFlyToId(id + ':' + Date.now()) // force re-fly on same id by changing key
  }

  // stable flyToId: we pass the actual id to Globe; use a counter effect instead
  const flyCounterRef = useRef(0)
  const [flyMarker, setFlyMarker] = useState(null)
  useEffect(() => {
    if (selectedId) {
      flyCounterRef.current++
      setFlyMarker(selectedId)
    }
  }, [selectedId])

  // search submission
  const runSearch = async (e) => {
    e?.preventDefault()
    const q = query.trim()
    if (!q) { setResults(null); return }
    setSearching(true); setError(null)
    try {
      const { matches } = await searchIntents(q, 25)
      setResults(matches)
      if (matches[0]) handleSelect(matches[0].intent_id)
    } catch (err) {
      setError(err.message)
    } finally {
      setSearching(false)
    }
  }

  const clearSearch = () => { setQuery(''); setResults(null) }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand" data-testid="brand-title">Setu</div>
        <form className="search" onSubmit={runSearch}>
          <input
            className="search__input"
            type="search"
            placeholder="Search intents — try “flatmate in Koramangala” or “cricket weekend”"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-testid="search-input"
          />
          <button
            type="submit"
            className="search__btn"
            disabled={searching || !query.trim()}
            data-testid="search-submit-btn"
          >
            {searching ? '…' : 'Search'}
          </button>
          {searchResults && (
            <button
              type="button"
              className="search__clear"
              onClick={clearSearch}
              data-testid="search-clear-btn"
            >
              ×
            </button>
          )}
        </form>
      </header>

      <main className="main">
        <Globe
          intents={intents}
          highlightedIds={highlightedIds}
          selectedId={selectedId}
          flyToId={flyMarker && `${flyMarker}::${flyCounterRef.current}`}
          onSelect={handleSelect}
        />
        <SidePanel
          items={panelItems}
          mode={searchResults ? 'search' : 'recent'}
          total={intents.length}
          loading={loading}
          error={error}
          selectedId={selectedId}
          onSelect={handleSelect}
          onClear={clearSearch}
        />
      </main>
    </div>
  )
}
