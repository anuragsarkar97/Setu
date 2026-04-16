# Distributed Agent Intent Bulletin — PRD

## Original Problem Statement
Build a "Distributed Agent Intent Bulletin" system where:
- Users have agent profiles with evolving preferences
- Agents generate and refine intents
- Intents are semantically matched using embeddings
- Matching is async
- If no match, intents can be forwarded to neighbor agents (small-world graph idea)
- System should feel distributed but can be centrally implemented

Constraints: 12-hour build hackathon MVP — prioritize simplicity over correctness.
Stack: Python FastAPI + MongoDB + FAISS.

## Architecture
```
/app/backend/
├── server.py              # App init, CORS, lifespan (FAISS init), mounting routers
├── db.py                  # MongoDB connection, ensure_indexes()
├── faiss_index.py         # In-memory vector search abstraction
├── embeddings.py          # AsyncOpenAI integration for text-embedding-3-small
├── geocode.py             # Google Maps API geocoding and Haversine distance
├── llm/
│   ├── clarification.txt      # Prompt: Asks clarifying questions if intent is vague
│   ├── extract_intent.txt     # Prompt: Structures intent (domain, budget, tags, location)
│   ├── extract_preference.txt # Prompt: Extracts temporary contextual preferences
│   └── extract_persona.txt    # Prompt: Extracts stable user facts
└── routers/
    ├── agents.py          # Profile CRUD, persona updates, preference merging
    ├── intents.py         # Intent 2-pass creation, LLM parallel pipelines
    ├── matching.py        # Semantic + location + FAISS/Mongo vector matching
    └── routing.py         # Small-world BFS matching logic across connected neighbors
```

## DB Schema
- `agents`: `{agent_id, name, preferences(dict), persona(dict), history(list), interactions(list), neighbors(list)}`
- `intents`: `{intent_id, agent_id, text, extracted(dict), embedding(list[float]), status, preferences_snapshot}`

## Key API Endpoints
- `POST /api/agents` — create agent
- `GET /api/agents/{id}` — full profile
- `PATCH /api/agents/{id}/preferences` — manual preference update
- `PATCH /api/agents/{id}/persona` — manual persona update
- `GET /api/agents/{id}/persona` — get persona
- `POST /api/intents` — create intent (2-pass: clarification → extract + embed + match)
- `GET /api/intents?agent_id=xxx` — list intents
- `POST /api/intents/{id}/regenerate` — re-run intent with latest profile
- `POST /api/matching/match` — semantic match
- `POST /api/matching/build-index` — rebuild FAISS
- `POST /api/routing/connect` — create bidirectional agent edge
- `POST /api/routing/route` — BFS routing through graph

## What's Been Implemented

### Phase 1 — Setup (DONE)
- FastAPI app, MongoDB (Motor), FAISS, supervisor config

### Phase 2 — Agent Profile (DONE)
- Agent CRUD with preferences (evolving, snapshotted into history)
- Interaction log per agent
- Neighbor list for graph

### Phase 3 — Intent Generation (DONE)
- 2-pass intent creation: Pass 1 = clarification questions, Pass 2 = extract + embed + match
- LLM: gpt-5.4-mini via AsyncOpenAI
- clarification.txt, extract_intent.txt prompts

### Phase 4 — Embeddings + Matching (DONE)
- OpenAI text-embedding-3-small (1536d)
- FAISS IndexFlatIP for in-memory cosine search
- MongoDB fallback for full scan
- Haversine distance geocoding filter
- Google Maps API for location resolution

### Phase 5 — Neighbor Routing (DONE)
- BFS through agent neighbor graph
- Each node does local intent matching
- Bidirectional edge management (connect/disconnect)

### Persona Feature (DONE — 2026-04-16)
- `extract_persona.txt` prompt for stable personal facts
- `persona` field on agent schema
- `PATCH /api/agents/{id}/persona` + `GET /api/agents/{id}/persona`
- `extract_persona_from_text()` + `save_persona()` in intents.py
- Auto-runs in parallel with preference extraction on every intent
- Persona context passed to `check_clarification` as "Who they are" — LLM never re-asks known facts
- Preference extraction: transient signals (location, availability, interests)
- Persona extraction: stable facts (name, pets, home, dietary, occupation)

## Prioritized Backlog

### P0
- Phase 6: Async match + notification
  - Background task on intent creation to find matches
  - `notifications` MongoDB collection
  - `GET /api/notifications/{agent_id}` to poll
  - `PATCH /api/notifications/{id}/read`

### P1
- Phase 7: Demo seed endpoint
  - `POST /api/demo/seed` — creates 5 agents, graph, intents, triggers full lifecycle
  - Returns trace of the whole distributed flow

### P2
- Frontend UI (explicitly deferred)
- `POST /api/demo/reset` to clean seed data

## 3rd Party Integrations
- OpenAI GPT-5.4-mini (chat) — OPENAI_API_KEY in backend/.env
- OpenAI text-embedding-3-small (embeddings) — same key
- Google Maps Geocoding API — GOOGLE_MAPS_API_KEY in backend/.env
