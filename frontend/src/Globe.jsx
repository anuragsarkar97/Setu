import React, { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

// OpenFreeMap "fiord" — free dark vector style (no API key needed).
// Vector tiles are required for the globe projection to render correctly.
const STYLE_URL = 'https://tiles.openfreemap.org/styles/fiord'

export default function Globe({ intents, highlightedIds, selectedId, onSelect, flyToId }) {
  const containerRef = useRef(null)
  const mapRef       = useRef(null)
  const markersRef   = useRef(new Map()) // intent_id -> marker
  const popupRef     = useRef(null)      // single reusable popup
  const intentsRef   = useRef([])        // latest intents for popup lookup

  // init map once
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE_URL,
      center: [78.9629, 22.5937],   // India
      zoom: 4.2,
      attributionControl: { compact: true },
    })

    map.on('load', () => {
      // set globe projection after style load (v5 API)
      try { map.setProjection({ type: 'globe' }) } catch (e) { console.warn('globe projection unsupported:', e) }

      // atmosphere around the globe
      try {
        map.setSky({
          'sky-color':         '#0a0e17',
          'horizon-color':     '#1a2540',
          'fog-color':         '#050810',
          'sky-horizon-blend': 0.6,
          'horizon-fog-blend': 0.5,
          'fog-ground-blend':  0.3,
        })
      } catch {}
    })

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    mapRef.current = map
    return () => map.remove()
  }, [])

  // sync markers when intents change
  useEffect(() => {
    intentsRef.current = intents
    const map = mapRef.current
    if (!map) return

    const existing = markersRef.current
    const nextIds  = new Set()

    for (const i of intents) {
      if (i.lat == null || i.lng == null) continue
      nextIds.add(i.intent_id)

      let marker = existing.get(i.intent_id)
      if (!marker) {
        const el = document.createElement('div')
        el.className = 'pin'
        el.title = i.summary || i.text
        el.addEventListener('click', (ev) => {
          ev.stopPropagation()
          onSelect?.(i.intent_id)
        })
        marker = new maplibregl.Marker({ element: el })
          .setLngLat([i.lng, i.lat])
          .addTo(map)
        existing.set(i.intent_id, marker)
      }
    }
    for (const [id, m] of existing) {
      if (!nextIds.has(id)) { m.remove(); existing.delete(id) }
    }
  }, [intents, onSelect])

  // apply highlight / selected classes
  useEffect(() => {
    for (const [id, m] of markersRef.current) {
      const el = m.getElement()
      const dim       = highlightedIds && !highlightedIds.has(id)
      const highlight = highlightedIds && highlightedIds.has(id)
      const selected  = id === selectedId
      el.classList.toggle('pin--dim', !!dim)
      el.classList.toggle('pin--highlight', !!highlight)
      el.classList.toggle('pin--selected', !!selected)
    }
  }, [highlightedIds, selectedId, intents])

  // fly-to + popup (flyToId pattern: "<id>::<counter>" so same id can re-fly)
  useEffect(() => {
    if (!flyToId) return
    const realId = String(flyToId).split('::')[0]
    const m = markersRef.current.get(realId)
    if (!m) return
    const ll = m.getLngLat()
    mapRef.current?.flyTo({
      center: [ll.lng, ll.lat],
      zoom: 9,
      speed: 1.2,
      curve: 1.4,
      essential: true,
    })

    // Show popup for this intent
    const intent = intentsRef.current.find((i) => i.intent_id === realId)
    if (!intent) return

    if (popupRef.current) popupRef.current.remove()

    const escape = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
    ))
    const tags = (intent.tags || []).slice(0, 4)
      .map((t) => `<span class="popup__tag">${escape(t)}</span>`).join('')
    const html = `
      <div class="popup">
        <div class="popup__top">
          <span class="popup__type">${escape(intent.intent_type || 'other')}</span>
          ${intent.location ? `<span class="popup__loc">📍 ${escape(intent.location)}</span>` : ''}
        </div>
        <div class="popup__text">${escape(intent.summary || intent.text || '')}</div>
        ${tags ? `<div class="popup__tags">${tags}</div>` : ''}
      </div>`

    popupRef.current = new maplibregl.Popup({
      offset: 18,
      closeButton: true,
      closeOnClick: true,
      maxWidth: '320px',
      className: 'intent-popup',
    })
      .setLngLat([ll.lng, ll.lat])
      .setHTML(html)
      .addTo(mapRef.current)
  }, [flyToId])

  return <div ref={containerRef} className="globe" />
}
