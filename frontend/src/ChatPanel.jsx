import React, { useEffect, useRef, useState } from 'react'
import { sendChat } from './api.js'

const GREETING = "hi! i can help you find intents on the map or post one for you. try \"flatmates in koramangala\" or \"i want to sell my bike\"."

export default function ChatPanel({ agentId, onResults, onIntentCreated }) {
  const [messages, setMessages] = useState([
    { role: 'assistant', content: GREETING },
  ])
  const [draft, setDraft]     = useState('')
  const [sending, setSending] = useState(false)
  const [convId, setConvId]   = useState(
    () => localStorage.getItem('setu_conversation_id') || null
  )
  const threadRef = useRef(null)

  useEffect(() => {
    const el = threadRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, sending])

  const send = async (e) => {
    e?.preventDefault()
    const msg = draft.trim()
    if (!msg || sending || !agentId) return

    setDraft('')
    setMessages((m) => [...m, { role: 'user', content: msg }])
    setSending(true)

    try {
      const res = await sendChat({ agentId, message: msg, conversationId: convId })
      setConvId(res.conversation_id)
      localStorage.setItem('setu_conversation_id', res.conversation_id)

      setMessages((m) => [
        ...m,
        ...(res.tool_events || []).map((ev) => ({ role: 'tool', event: ev })),
        { role: 'assistant', content: res.reply },
      ])

      onResults?.(res.highlight_intent_ids || [])

      const created = (res.tool_events || []).find(
        (ev) => ev.tool === 'create_intent' && ev.result?.status === 'created'
      )
      if (created) onIntentCreated?.(created.result.intent)
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: `hmm — ${err.message}. try again?` },
      ])
    } finally {
      setSending(false)
    }
  }

  const resetConversation = () => {
    localStorage.removeItem('setu_conversation_id')
    setConvId(null)
    setMessages([{ role: 'assistant', content: GREETING }])
    onResults?.([])
  }

  return (
    <section className="chat" data-testid="chat-panel">
      <header className="chat__header">
        <div className="chat__brand">Setu</div>
        <div className="chat__header-right">
          <div className="chat__hint">talk to the bulletin</div>
          {convId && (
            <button
              className="chat__reset"
              onClick={resetConversation}
              data-testid="chat-reset-btn"
              title="Start a new conversation"
            >
              new
            </button>
          )}
        </div>
      </header>

      <div className="chat__thread" ref={threadRef}>
        {messages.map((m, i) => (
          <MessageRow key={i} m={m} />
        ))}
        {sending && (
          <div className="msg msg--typing" data-testid="chat-typing">
            thinking<span className="dots">…</span>
          </div>
        )}
      </div>

      <form className="chat__input" onSubmit={send}>
        <input
          type="text"
          placeholder="search or post an intent…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={sending || !agentId}
          data-testid="chat-input"
          autoFocus
        />
        <button
          type="submit"
          disabled={sending || !draft.trim() || !agentId}
          data-testid="chat-send-btn"
          aria-label="send"
        >
          {sending ? '…' : '→'}
        </button>
      </form>
    </section>
  )
}

function MessageRow({ m }) {
  if (m.role === 'user') {
    return <div className="msg msg--user" data-testid="msg-user">{m.content}</div>
  }
  if (m.role === 'assistant') {
    return <div className="msg msg--assistant" data-testid="msg-assistant">{m.content}</div>
  }
  if (m.role === 'tool') {
    const e = m.event
    const icon = e.tool === 'search_intents' ? '🔍' : e.tool === 'create_intent' ? '📌' : '•'
    let label = e.tool
    if (e.tool === 'search_intents') {
      const n = e.result?.match_count ?? (e.result?.matches?.length ?? 0)
      const q = e.args?.query || ''
      label = `searched "${q}" — ${n} ${n === 1 ? 'match' : 'matches'}`
    } else if (e.tool === 'create_intent') {
      if (e.result?.status === 'created') {
        const t = e.result?.intent?.summary || e.result?.intent?.text || 'intent'
        label = `posted: ${t}`
      } else if (e.result?.error) {
        label = `couldn't post: ${e.result.error}`
      } else {
        label = 'creating intent…'
      }
    }
    return (
      <div className="msg msg--tool" data-testid={`tool-event-${e.tool}`}>
        <span className="msg__tool-icon">{icon}</span>
        <span className="msg__tool-label">{label}</span>
      </div>
    )
  }
  return null
}
