"""
Tool implementations exposed to the chat LLM.

Shape is intentionally LLM-friendly: compact, explicit `status` fields,
no embeddings in the response. The chat LLM is responsible for collecting
all required details from the user BEFORE calling `create_intent` — the
tool itself is a dumb extract + embed + persist step.
"""
from embeddings import embed
from geocode import geocode as geocode_location
import store
from utils.clarify import extract_intent_structure
from utils.create import create_intent as persist_intent
from utils.search import search_by_text, search_by_vector


async def tool_search_intents(
    agent_id: str,
    query: str,
    top_n: int = 5,
    threshold: float = 0.5,
) -> dict:
    """Pure semantic search. Excludes the caller's own intents."""
    q = (query or "").strip()
    if not q:
        return {"error": "empty_query"}
    matches = await search_by_text(q, {}, agent_id, top_n, threshold)
    return {
        "query":        q,
        "match_count":  len(matches),
        "matches":      matches,
    }


async def _maybe_geocode(extracted: dict) -> dict:
    loc = (extracted.get("location_query") or "").strip()
    if not loc or extracted.get("radius", 10.0) == 0.0:
        return extracted
    try:
        lat, lng = await geocode_location(loc)
        return {**extracted, "lat": lat, "lng": lng}
    except Exception as e:
        print(f"[chat_tools] geocode failed for {loc!r}: {e}")
        return extracted


async def tool_create_intent(
    agent_id: str,
    text: str,
    answers: str = "",
    previous_questions: list | None = None,
) -> dict:
    """
    Extract structured fields from the user's intent text, embed it, and
    persist. If the LLM had to gather follow-up details, it should fold
    them into the `text` field itself — or, equivalently, pass them via
    `answers`/`previous_questions` so the final embedded string is
    comprehensive.
    """
    t = (text or "").strip()
    if not t:
        return {"error": "empty_text"}

    agent = store.get_agent(agent_id)
    if not agent:
        return {"error": "agent_not_found"}

    prefs = (agent.get("preferences") or "").strip()

    # Fold in any follow-up answers so the embedded + extracted string is complete
    final_text = t
    if answers:
        final_text = f"{t} — {answers}"

    extracted = await extract_intent_structure(final_text, prefs)
    extracted = await _maybe_geocode(extracted)
    embedding = await embed(final_text)

    new_doc = await persist_intent(agent_id, final_text, extracted, embedding, prefs)
    nearby = search_by_vector(embedding, extracted, agent_id, 3, 0.4)

    return {
        "status": "created",
        "intent": {
            "intent_id":    new_doc["intent_id"],
            "text":         new_doc["text"],
            "summary":      extracted.get("summary", "") or "",
            "intent_type":  extracted.get("intent_type", "") or "",
            "location":     extracted.get("location_query", "") or "",
            "lat":          extracted.get("lat"),
            "lng":          extracted.get("lng"),
            "tags":         extracted.get("tags") or [],
        },
        "nearby_matches": nearby,
    }
