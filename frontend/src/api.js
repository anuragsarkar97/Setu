const BASE = import.meta.env.VITE_BACKEND_URL || ''

async function req(path, opts = {}) {
  const r = await fetch(`${BASE}${path}`, opts)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export const fetchIntents = () => req('/api/intents')

export const searchIntents = (query, topN = 25) =>
  req('/api/intent/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_n: topN, threshold: 0.25 }),
  })
