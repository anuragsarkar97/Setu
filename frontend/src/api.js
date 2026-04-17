const BASE = import.meta.env.VITE_BACKEND_URL || ''

async function req(path, opts = {}) {
  const r = await fetch(`${BASE}${path}`, opts)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export const fetchIntents = () => req('/api/intents')

export const createAgent = (name) =>
  req('/api/agents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })

export const sendChat = ({ agentId, message, conversationId }) =>
  req('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      agent_id: agentId,
      message,
      conversation_id: conversationId || undefined,
    }),
  })
