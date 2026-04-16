"""
Unified intent router.

Single public endpoint: POST /api/intent/route

Acts as an intelligent dispatcher that decides what the user actually wants:
  - create a new intent
  - update an existing intent (new doc written first, old deleted after)
  - delete an existing intent
  - clarify — returned when the intended action is create/update but the intent
    is missing critical details

The router itself is stateless; side-effects (persona/preference saves, FAISS
add, interaction logs) happen inside the reused helpers in intents.py /
matching.py.

Older endpoints in intents.py remain mounted so their helpers can be reused
and so existing clients keep working.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Body, HTTPException
from openai import AsyncOpenAI

import faiss_index
from db import get_db
from routers.intents import _MODEL, _run_pipeline  # reuse existing pipeline

load_dotenv()

router = APIRouter(prefix="/api/intent", tags=["intent-router"])

_openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
_LLM_DIR = Path(__file__).parent.parent / "llm"
_ROUTE_ACTION_PROMPT = (_LLM_DIR / "route_action.txt").read_text().strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _classify_action(
    text: str, existing_intents: list[dict], target_hint: str
) -> dict:
    """
    Ask the LLM which action the user's message represents.
    Falls back to {'create', '', ''} on any failure so the request still
    produces something useful.
    """
    existing_str = "\n".join(
        f"- [{i['intent_id']}] \"{i['text']}\""
        for i in existing_intents
    ) or "(none)"

    user_msg = (
        f"User message: \"{text}\"\n"
        f"Existing intents:\n{existing_str}\n"
        f"Target hint: \"{target_hint or ''}\""
    )

    try:
        resp = await _openai.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _ROUTE_ACTION_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        print(f"[router] classifier failed: {e}")
        parsed = {}

    action = parsed.get("action", "create")
    target = (parsed.get("target_intent_id") or "").strip()
    reasoning = parsed.get("reasoning", "")

    # Safety: update/delete with an unknown id ⇒ fall back to create
    valid_ids = {i["intent_id"] for i in existing_intents}
    if action in ("update", "delete") and target not in valid_ids:
        return {
            "action": "create",
            "target_intent_id": "",
            "reasoning": f"fallback to create (invalid target). original: {reasoning}",
        }
    if action == "create":
        target = ""
    if action not in ("create", "update", "delete"):
        return {"action": "create", "target_intent_id": "", "reasoning": "fallback: unknown action"}

    return {"action": action, "target_intent_id": target, "reasoning": reasoning}


async def _delete_intent(intent_id: str, agent_id: str) -> dict:
    """Delete an intent doc and log on the owning agent."""
    result = await get_db().intents.delete_one(
        {"intent_id": intent_id, "agent_id": agent_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Intent not found: {intent_id}")

    await get_db().agents.update_one(
        {"agent_id": agent_id},
        {"$push": {"interactions": {
            "event": "intent_deleted",
            "data": {"intent_id": intent_id},
            "timestamp": datetime.now(timezone.utc),
        }}},
    )
    return {"intent_id": intent_id, "deleted": True}


async def _create_intent_doc(
    agent_id: str, text: str, extracted: dict, embedding: list[float], prefs: dict,
) -> dict:
    """Persist a new intent, add to FAISS, log creation on the agent."""
    now = datetime.now(timezone.utc)
    doc = {
        "intent_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "text": text,
        "extracted": extracted,
        "embedding": embedding,
        "status": "active",
        "preferences_snapshot": prefs,
        "created_at": now,
        "updated_at": now,
    }
    await get_db().intents.insert_one(doc)
    doc.pop("_id", None)
    faiss_index.add(doc["intent_id"], embedding)

    await get_db().agents.update_one(
        {"agent_id": agent_id},
        {"$push": {"interactions": {
            "event": "intent_created",
            "data": {"intent_id": doc["intent_id"], "text": doc["text"]},
            "timestamp": now,
        }}},
    )
    return doc


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/route")
async def route_intent(body: dict = Body(...)):
    """
    Intelligent single-endpoint router.

    Body:
      {
        "agent_id": "...",                  // required
        "text": "...",                      // required — user's natural-language message
        "target_intent_id": "...",          // optional hint for update/delete
        "answers": "...",                   // optional, pass-2 clarification answers
        "previous_questions": [...],        // optional, pass-2 clarification questions
        "top_n": 5,                         // optional match limit
        "threshold": 0.7                    // optional match threshold
      }

    Responses (one of):
      { "action": "clarify",  "questions": [...], "acknowledgements": [...],
        "intended_action": "create"|"update", "target_intent_id": "..." }
      { "action": "created",  "intent": {...}, "matches": [...], "index_used": "..." }
      { "action": "updated",  "intent": {...}, "replaced_intent_id": "...",
        "matches": [...], "index_used": "..." }
      { "action": "deleted",  "intent_id": "..." }

    Every response also includes "reasoning" from the classifier.
    """
    agent_id = body.get("agent_id")
    text = (body.get("text") or "").strip()
    target_hint = (body.get("target_intent_id") or "").strip()
    answers = (body.get("answers") or "").strip()
    previous_questions = body.get("previous_questions") or []

    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    agent = await get_db().agents.find_one({"agent_id": agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Compact list of the agent's active intents — classifier context
    cursor = get_db().intents.find(
        {"agent_id": agent_id, "status": "active"},
        {"_id": 0, "intent_id": 1, "text": 1, "created_at": 1},
    ).sort("created_at", -1)
    existing_intents = await cursor.to_list(length=50)

    decision = await _classify_action(text, existing_intents, target_hint)
    action = decision["action"]
    target_id = decision["target_intent_id"]
    reasoning = decision["reasoning"]

    # --- delete branch -----------------------------------------------------
    if action == "delete":
        out = await _delete_intent(target_id, agent_id)
        return {"action": "deleted", "reasoning": reasoning, **out}

    # --- create / update share the full pipeline ---------------------------
    prefs = agent.get("preferences", {})
    persona = agent.get("persona", {})

    clarification, final_text, extracted, embedding = await _run_pipeline(
        text, prefs, persona, answers,
        agent_id=agent_id, previous_questions=previous_questions,
    )

    if clarification:
        return {
            "action": "clarify",
            "reasoning": reasoning,
            "intended_action": action,              # "create" or "update"
            "target_intent_id": target_id or None,
            "questions": clarification["questions"],
            "acknowledgements": clarification["acknowledgements"],
        }

    # Write the new intent first — if the old-doc delete fails later we still
    # preserve the new state.
    new_doc = await _create_intent_doc(
        agent_id, final_text, extracted, embedding, prefs
    )

    replaced_id = None
    if action == "update":
        try:
            await _delete_intent(target_id, agent_id)
            replaced_id = target_id
        except HTTPException:
            # Target already gone — degrade to a plain create silently
            action = "create"

    # Match against newly written intent
    from routers.matching import run_match
    top_n = int(body.get("top_n", 5))
    threshold = float(body.get("threshold", 0.7))
    matches, index_used = await run_match(new_doc, top_n, threshold)

    return {
        "action": "updated" if action == "update" else "created",
        "reasoning": reasoning,
        "intent": new_doc,
        "replaced_intent_id": replaced_id,
        "matches": matches,
        "index_used": index_used,
    }
