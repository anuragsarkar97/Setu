"""
LLM-based reranker — second pass after vector search + hard filters.

The vector search finds semantically similar intents.
The reranker asks: "given what both parties actually need, how compatible are they?"

It understands things cosine similarity cannot:
  - "vegetarian household" matches "veg non-smoker looking for room" better than
    "veg household, smokers ok" even if both have similar embeddings
  - A budget of 12k doesn't fit a listing asking 18k
  - "casual badminton weekend" ≠ "competitive training 5am daily"

Input:  source intent dict + list of candidate result dicts (from search_by_vector)
Output: same list re-ordered by reranker score, with score + reason added
"""
import json

from llm.api import openai_client
from utils.clarify import MODEL

_SYSTEM = """\
You are a compatibility ranker for a people-matching platform called Sangam.

You receive one SOURCE intent and a list of CANDIDATE intents.
Your job: rank candidates by mutual compatibility with the source — how well would
both parties' needs be satisfied if they connected?

Scoring rubric (0.00–1.00):
  0.90–1.00  Near-perfect match. Budget, lifestyle, location, and goal all align.
  0.70–0.89  Good match. Core signals align; minor gaps that are negotiable.
  0.50–0.69  Partial match. Some important signals align; some don't.
  0.30–0.49  Weak match. Similar domain but meaningful incompatibilities.
  0.00–0.29  Poor match. Explicit incompatibility on a core signal.

HARD INCOMPATIBILITIES — score ≤ 0.15, regardless of other signals:
  1. Seeking a PERSON vs seeking a PROPERTY.
     - Source wants a flatmate/roommate (a person to share with).
     - Candidate is looking for a flat/house/room to rent (a property).
     These parties want completely different things. Not a match.
  2. Explicit "no roommates", "no flatmates", "no sharing", "couples only",
     "family only", "single occupancy" in candidate — source is a flatmate seeker.
  3. One party explicitly requires online/remote; the other requires in-person.
  4. Dietary: vegetarian source + candidate explicitly says non-veg household.
  5. Smoking: non-smoker source + candidate explicitly allows or is a smoker household.

General rules:
- Score each candidate independently.
- A missing signal is NOT a blocker — only a stated incompatibility is.
- Penalise budget gaps that cannot overlap even with negotiation.
- Reward specificity alignment: when both sides have rich, matching details.
- Reason must be one sentence, ≤ 15 words.

Output STRICT JSON — no markdown, no commentary:
{
  "ranked": [
    {"intent_id": "...", "score": 0.00, "reason": "..."},
    ...
  ]
}

Include ALL candidates in ranked output, even low-scoring ones.
"""


def _brief(intent: dict) -> dict:
    """Compact representation for the reranker prompt — avoids token bloat."""
    ext = intent.get("extracted") or {}
    return {
        "intent_id":   intent.get("intent_id", ""),
        "text":        (intent.get("text") or "")[:300],
        "intent_type": ext.get("intent_type") or intent.get("intent_type", ""),
        "location":    ext.get("location_query") or intent.get("location", ""),
        "budget_min":  ext.get("budget_min") or intent.get("budget_min") or None,
        "budget_max":  ext.get("budget_max") or intent.get("budget_max") or None,
        "dietary":     ext.get("dietary") or None,
        "smoking":     ext.get("smoking") or None,
        "gender_pref": ext.get("gender_pref") or None,
        "skill_level": ext.get("skill_level") or None,
        "format":      ext.get("format") or None,
        "tags":        (ext.get("tags") or intent.get("tags") or [])[:5],
    }


async def rerank(source_doc: dict, candidates: list[dict]) -> list[dict]:
    """
    Rerank candidates by compatibility with source_doc.

    Args:
        source_doc:  The newly created intent doc (has 'extracted' + 'text').
        candidates:  Results from search_by_vector (each has intent_id, text, etc.).

    Returns:
        candidates list re-ordered by reranker score (descending).
        Each result gets two new fields: rerank_score (float) and rerank_reason (str).
    """
    if not candidates:
        return candidates

    # Single candidate — no point calling LLM
    if len(candidates) == 1:
        candidates[0]["rerank_score"] = candidates[0].get("relevance", 0.5)
        candidates[0]["rerank_reason"] = "only result"
        return candidates

    # Build a lookup from intent_id → candidate dict (for merging scores back)
    by_id = {c["intent_id"]: c for c in candidates}

    # Build the prompt payload
    # For candidates, load full docs from store to get extracted fields
    import store as _store
    cand_docs = []
    for c in candidates:
        full = _store.all_intents()  # already loaded in memory
        doc = next((i for i in full if i["intent_id"] == c["intent_id"]), None)
        if doc:
            brief = _brief(doc)
        else:
            brief = {
                "intent_id": c["intent_id"],
                "text": c.get("text", "")[:300],
                "intent_type": c.get("intent_type", ""),
                "location": c.get("location", ""),
                "budget_min": None, "budget_max": None,
                "tags": c.get("tags", [])[:5],
            }
        cand_docs.append(brief)

    user_msg = json.dumps({
        "source": _brief(source_doc),
        "candidates": cand_docs,
    }, ensure_ascii=False)

    try:
        resp = await openai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system",  "content": _SYSTEM},
                {"role": "user",    "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        ranked_raw = data.get("ranked") or []
    except Exception as e:
        print(f"[rerank] LLM call failed: {e}")
        return candidates  # fall back to original order

    # Merge reranker scores back into candidate dicts
    scored: list[dict] = []
    seen: set[str] = set()

    for item in ranked_raw:
        iid   = item.get("intent_id", "")
        score = float(item.get("score", 0.0))
        reason = str(item.get("reason", ""))

        if iid not in by_id or iid in seen:
            continue
        seen.add(iid)

        c = dict(by_id[iid])          # copy so we don't mutate the original
        c["rerank_score"]  = round(score, 4)
        c["rerank_reason"] = reason
        scored.append(c)

    # Any candidates the LLM dropped — append at end with score=0
    for iid, c in by_id.items():
        if iid not in seen:
            c = dict(c)
            c["rerank_score"]  = 0.0
            c["rerank_reason"] = "not ranked by model"
            scored.append(c)

    scored.sort(key=lambda r: r["rerank_score"], reverse=True)
    return scored
