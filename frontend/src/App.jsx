import React, { useEffect, useRef, useState } from 'react'
import Globe from './Globe.jsx'
import ChatPanel from './ChatPanel.jsx'
import { createAgent, fetchIntents } from './api.js'

/** Read ?agent_id=... from the URL; returns '' if absent. */
function getAgentFromUrl() {
  try {
    const p = new URLSearchParams(window.location.search)
    return (p.get('agent_id') || '').trim()
  } catch { return '' }
}

/** Put ?agent_id=... into the URL without navigating / reloading. */
function setAgentInUrl(id) {
  const u = new URL(window.location.href)
  u.searchParams.set('agent_id', id)
  window.history.replaceState(null, '', u.toString())
}

export default function App() {
  const [agentId, setAgentId] = useState(() => getAgentFromUrl())
  const [intents, setIntents] = useState([])
  const [highlightedIds, setHighlightedIds] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [flyMarker, setFlyMarker] = useState(null)
  const flyCounter = useRef(0)

  // bootstrap an anonymous agent if the URL doesn't already specify one
  useEffect(() => {
    if (agentId) return
    const slug = `Guest-${Math.random().toString(36).slice(2, 8)}`
    createAgent(slug)
      .then(({ agent_id }) => {
        setAgentInUrl(agent_id)
        setAgentId(agent_id)
      })
      .catch((e) => console.error('agent bootstrap:', e))
  }, [agentId])

  const refetchIntents = () =>
    fetchIntents()
      .then(setIntents)
      .catch((e) => console.error('fetchIntents:', e))

  useEffect(() => { refetchIntents() }, [])

  const selectAndFly = (id) => {
    setSelectedId(id)
    flyCounter.current += 1
    setFlyMarker(id)
  }

  const handleChatResults = (intentIds) => {
    if (intentIds && intentIds.length > 0) {
      setHighlightedIds(new Set(intentIds))
      selectAndFly(intentIds[0])
    }
  }

  const handleIntentCreated = () => refetchIntents()
  const handlePinSelect = (id) => selectAndFly(id)

  return (
    <div className="app">
      <ChatPanel
        agentId={agentId}
        onResults={handleChatResults}
        onIntentCreated={handleIntentCreated}
      />
      <main className="stage">
        <Globe
          intents={intents}
          highlightedIds={highlightedIds}
          selectedId={selectedId}
          flyToId={flyMarker ? `${flyMarker}::${flyCounter.current}` : null}
          onSelect={handlePinSelect}
        />
      </main>
    </div>
  )
}
