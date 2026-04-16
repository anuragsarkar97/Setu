"""
Single endpoint: POST /api/intent/route

Stateless dispatcher. Router LLM decides clarify | search | respond.
- clarify: not enough signal, return questions to user
- search:  enough signal, create intent + find matches
- respond: not a matching intent, return a plain response
"""
import json
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

import asyncio

import store
from utils.clarify import MODEL, extract_intent_structure, save_preferences, save_persona, refresh_persona
from utils.create import create_intent
from utils.search import search_by_vector, search_by_text
from utils.rerank import rerank
from embeddings import embed
from geocode import geocode as geocode_location

from llm.api import openai_client

router = APIRouter(prefix="/api/intent", tags=["intent-router"])

ROUTER_PROMPT = (Path(__file__).parent.parent / "llm" / "router_prompt.md").read_text().strip()


async def apply_router(raw_intent: str, persona: str = "") -> dict:
    """Call the router LLM. Returns full parsed response dict."""
    try:
        resp = await openai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": json.dumps({
                    "raw_intent": raw_intent,
                    "persona": persona or None,
                })},
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
        print("[router]", parsed)
    except Exception as e:
        print(f"[router] failed: {e}")
        parsed = {}

    action = parsed.get("action")
    if action not in ("clarify", "search", "respond"):
        action = "respond"

    return {
        "action": action,
        "reasoning": parsed.get("reasoning", ""),
        "questions": parsed.get("questions", []),
        "enriched_intent": parsed.get("enriched_intent"),
        "response": parsed.get("response", ""),
    }


@router.post("/route")
async def route_intent(body: dict = Body(...)):
    """
    Body:
      {
        "agent_id": "...",           // required
        "text":     "...",           // required
        "answers":  "...",           // optional — pass-2 answers to clarification questions
        "previous_questions": [...], // optional — questions from pass-1
        "top_n":    5,               // optional, default 5
        "threshold": 0.7             // optional, default 0.7
      }

    Responses (one of):
      { "action": "clarify",   reasoning, questions }
      { "action": "created",   reasoning, intent, matches }
      { "action": "responded", reasoning, response }
    """
    agent_id = body.get("agent_id")
    text = (body.get("text") or "").strip()
    answers = (body.get("answers") or "").strip()
    top_n = body.get("top_n", 5)
    threshold = body.get("threshold", 0.5)

    if not agent_id:
        raise HTTPException(400, "agent_id is required")
    if not text:
        raise HTTPException(400, "text is required")

    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    persona = agent.get("persona", "") or ""
    prefs = agent.get("preferences", "") or ""

    # Build raw_intent: original text + any pass-2 answers
    raw_intent = f"{text}\n{answers}".strip() if answers else text

    decision = await apply_router(raw_intent, persona)
    action = decision["action"]
    reasoning = decision["reasoning"]

    # --- CLARIFY: not enough signal yet ---
    if action == "clarify":
        return {
            "action": "clarify",
            "reasoning": reasoning,
            "questions": decision["questions"],
        }

    # --- RESPOND: not a matching intent ---
    if action == "respond":
        return {
            "action": "responded",
            "reasoning": reasoning,
            "response": decision["response"],
        }

    # --- SEARCH: router has enough signal, create intent + find matches ---
    enriched = decision.get("enriched_intent") or {}

    # Persist any inferred preferences / persona signals
    if enriched.get("soft_signals") and agent_id:
        await save_preferences(agent_id, "; ".join(enriched["soft_signals"]))

    final_text = enriched.get("embedding_query") or raw_intent
    extracted, embedding = await _extract_and_embed(final_text, prefs, enriched)

    new_doc = await create_intent(agent_id, final_text, extracted, embedding, prefs)

    # Fire-and-forget: synthesize updated persona in the background
    asyncio.create_task(refresh_persona(agent_id, final_text, extracted))

    # Stage 1: vector search + hard filters (overfetch for reranker)
    candidates = search_by_vector(new_doc["embedding"], extracted, agent_id, top_n * 3, threshold)

    # Stage 2: LLM rerank — reason about mutual compatibility
    matches = await rerank(new_doc, candidates)
    # Drop results the reranker judged as poor matches (explicit incompatibilities)
    matches = [m for m in matches if m.get("rerank_score", 0) >= 0.55]
    matches = matches[:top_n]

    return {"action": "created", "reasoning": reasoning, "intent": _fmt_intent(new_doc), "matches": matches}


@router.post("/search")
async def search_intents(body: dict = Body(...)):
    """
    Direct text search — no routing, no intent creation.

    Body:
      {
        "query":      "...",    // required — natural language search query
        "agent_id":   "...",    // optional — exclude this agent's own intents
        "top_n":      5,        // optional, default 5
        "threshold":  0.5       // optional, default 0.5
      }

    Returns list of matching intents sorted by relevance.
    """
    query = (body.get("query") or "").strip()
    agent_id = body.get("agent_id", "")
    top_n = body.get("top_n", 5)
    threshold = body.get("threshold", 0.5)

    if not query:
        raise HTTPException(400, "query is required")

    matches = await search_by_text(query, {}, agent_id, top_n, threshold)
    return {"query": query, "matches": matches}


def _fmt_intent(doc: dict) -> dict:
    """Strip internal fields before returning intent to the client."""
    ext = doc.get("extracted") or {}
    return {
        "intent_id":    doc["intent_id"],
        "text":         doc["text"],
        "status":       doc["status"],
        "created_at":   doc["created_at"],
        "intent_type":  ext.get("intent_type", ""),
        "summary":      ext.get("summary", ""),
        "location":     ext.get("location_query", ""),
        "budget_min":   ext.get("budget_min") or None,
        "budget_max":   ext.get("budget_max") or None,
        "tags":         ext.get("tags") or [],
    }


async def _extract_and_embed(text: str, prefs: str, enriched: dict):
    """Build structured intent fields. Prefers router's enriched_intent over a fresh LLM call."""
    from utils.search import INTENT_TYPE_TO_DOMAIN

    if enriched:
        from utils.search import parse_timeline

        hf = enriched.get("hard_filters") or {}
        intent_type = enriched.get("intent_type", "")
        domain = INTENT_TYPE_TO_DOMAIN.get(intent_type.lower(), 15)

        _offering_types = {"selling", "offering"}
        intent_dir = 2 if intent_type.lower() in _offering_types else 1

        # Parse timeline string → unix timestamps for hard-filter comparison
        timeline_str = hf.get("timeline")
        tl_parsed    = parse_timeline(timeline_str) if timeline_str else None
        time_start   = tl_parsed[0] if tl_parsed else 0
        time_end     = tl_parsed[1] if tl_parsed else 0

        urgency = bool(hf.get("urgency", False))
        flags   = 0
        if urgency:
            flags |= 1   # bit 0 = urgent
        # bit 1 = remote_ok (set by caller if needed)

        extracted = {
            "intent_type":    intent_type,
            "summary":        enriched.get("summary", ""),
            "location_query": hf.get("location") or "",
            "budget_min":     hf.get("budget_min") or 0,
            "budget_max":     hf.get("budget_max") or 0,
            "tags":           enriched.get("soft_signals") or [],
            # hard_filter fields — stored for matching
            "dietary":        hf.get("dietary"),
            "smoking":        hf.get("smoking"),
            "gender_pref":    hf.get("gender_pref"),
            "urgency":        urgency,
            "item_type":      hf.get("item_type"),
            "skill_level":    hf.get("skill_level"),
            "format":         hf.get("format"),
            "timeline":       timeline_str,
            # numeric fields (timestamps now populated from router's timeline)
            "domain":     domain,
            "type":       intent_dir,
            "radius":     10.0,
            "time_start": time_start,
            "time_end":   time_end,
            "flags":      flags,
        }
    else:
        extracted = await extract_intent_structure(text, prefs)

    embedding = await embed(text)

    if extracted.get("location_query"):
        try:
            lat, lng = await geocode_location(extracted["location_query"])
            extracted["lat"], extracted["lng"] = lat, lng
        except Exception as e:
            print(f"[geocode] failed for '{extracted['location_query']}': {e}")

    return extracted, embedding
