import React, { useEffect, useRef, useState } from 'react'
import Globe from './Globe.jsx'
import ChatPanel from './ChatPanel.jsx'
import { createAgent, fetchIntents } from './api.js'

export default function App() {
  const [agentId, setAgentId] = useState(() => localStorage.getItem('setu_agent_id') || null)
  const [intents, setIntents] = useState([])
  const [highlightedIds, setHighlightedIds] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [flyMarker, setFlyMarker] = useState(null)
  const flyCounter = useRef(0)

  // bootstrap an anonymous agent on first visit
  useEffect(() => {
    if (agentId) return
    const slug = `Guest-${Math.random().toString(36).slice(2, 8)}`
    createAgent(slug)
      .then(({ agent_id }) => {
        localStorage.setItem('setu_agent_id', agent_id)
        setAgentId(agent_id)
      })
      .catch((e) => console.error('agent bootstrap:', e))
  }, [agentId])

  const refetchIntents = () =>
    fetchIntents()
      .then(setIntents)
      .catch((e) => console.error('fetchIntents:', e))

  useEffect(() => {
    refetchIntents()
  }, [])

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

  const handleIntentCreated = () => {
    refetchIntents()
  }

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
