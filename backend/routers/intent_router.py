"""
Single endpoint: POST /api/intent/route

Stateless dispatcher. LLM decides create vs search; clarification surfaces
when the "create" path needs more detail.
"""
import json
import os
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from openai import AsyncOpenAI

import store
from utils.clarify import MODEL, clarify_and_extract
from utils.create import create_intent
from utils.search import search_by_text, search_by_vector

router = APIRouter(prefix="/api/intent", tags=["intent-router"])

_openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
_PROMPT = (Path(__file__).parent.parent / "llm" / "route_action.txt").read_text().strip()


async def _classify(text: str) -> dict:
    """Classify the user's message as create | search. Falls back to 'create' on error."""
    try:
        resp = await _openai.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": f'User message: "{text}"'},
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
        print(parsed)
    except Exception as e:
        print(f"[router] classify failed: {e}")
        parsed = {}
    action = parsed.get("action")
    if action not in ("create", "search"):
        action = "create"
    return {"action": action, "reasoning": parsed.get("reasoning", "")}


@router.post("/route")
async def route_intent(body: dict = Body(...)):
    """
    Body:
      {
        "agent_id": "...",                  // required
        "text": "...",                      // required
        "answers": "...",                   // optional — pass-2 clarification answers
        "previous_questions": [...],        // optional — pass-2 clarification questions
        "top_n": 5,                         // optional
        "threshold": 0.7                    // optional
      }

    Responses (one of):
      { "action": "clarify",  questions, acknowledgements }
      { "action": "created",  intent, matches }
      { "action": "searched", query, results }
    """
    agent_id = body.get("agent_id")
    text = (body.get("text") or "").strip()
    answers = (body.get("answers") or "").strip()
    previous_questions = body.get("previous_questions") or []
    top_n = int(body.get("top_n", 5))
    threshold = float(body.get("threshold", 0.7))

    if not agent_id:
        raise HTTPException(400, "agent_id is required")
    if not text:
        raise HTTPException(400, "text is required")

    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    decision = await _classify(text)
    action, reasoning = decision["action"], decision["reasoning"]

    if action == "search":
        results = await search_by_text(text, agent_id, top_n, threshold)
        return {"action": "searched", "reasoning": reasoning, "query": text, "results": results}

    # action == "create"
    prefs = agent.get("preferences", {})
    persona = agent.get("persona", {})

    clarification, final_text, extracted, embedding = await clarify_and_extract(
        text, prefs, persona, answers,
        agent_id=agent_id, previous_questions=previous_questions,
    )
    if clarification:
        return {
            "action": "clarify",
            "reasoning": reasoning,
            "questions": clarification["questions"],
            "acknowledgements": clarification["acknowledgements"],
        }

    new_doc = await create_intent(agent_id, final_text, extracted, embedding, prefs)
    matches = search_by_vector(new_doc["embedding"], extracted, agent_id, top_n, threshold)
    return {"action": "created", "reasoning": reasoning, "intent": new_doc, "matches": matches}
