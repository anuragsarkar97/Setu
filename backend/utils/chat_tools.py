"""
The ONE tool exposed to the chat LLM: `route_intent`.

Wraps the same code path as `POST /api/intent/route`. The chat LLM is not
responsible for deciding search vs create vs clarify vs respond — the
router handles all of that. The chat LLM's only job is to turn the
router's structured output into natural conversation.

IMPORTANT — conversation-aware text construction:

The LLM's `text` argument is treated as informational. The wrapper rebuilds
the router's `raw_intent` from EVERY user turn in this conversation so that
info gathered across multiple clarify rounds doesn't get lost.

    User: "i want to sell my sofa"
    User: "5k"
    User: "indiranagar"
    User: "good condition, pickup from home"

    → router receives:
        i want to sell my sofa
        5k
        indiranagar
        good condition, pickup from home

The router's LLM synthesises the final embedding_query from the complete
picture, so the created intent reflects the whole conversation.
"""
from fastapi import HTTPException

from routers.intent_router import route_intent as _route_handler


async def tool_route_intent(
    agent_id: str,
    text: str,
    answers: str = "",
    previous_questions: list | None = None,
    conversation: dict | None = None,   # injected by chat.py, not part of the tool schema
) -> dict:
    """Invoke the intent router with the full conversation as context."""
    raw = _build_raw_intent(conversation, fallback_text=text, fallback_answers=answers)

    body = {
        "agent_id":           agent_id,
        "text":               raw,
        "answers":            "",                    # already folded into text
        "previous_questions": previous_questions or [],
    }
    try:
        return await _route_handler(body)
    except HTTPException as e:
        return {"error": e.detail, "status_code": e.status_code}
    except Exception as e:
        return {"error": str(e)}


def _build_raw_intent(conversation, fallback_text: str, fallback_answers: str) -> str:
    """
    Concatenate every user turn from the conversation into one multi-line
    string. Falls back to the LLM-provided text/answers if there's no
    conversation yet.
    """
    if conversation and isinstance(conversation.get("turns"), list):
        user_msgs = [
            (t.get("content") or "").strip()
            for t in conversation["turns"]
            if t.get("role") == "user" and (t.get("content") or "").strip()
        ]
        if user_msgs:
            return "\n".join(user_msgs)

    parts = [(fallback_text or "").strip()]
    if fallback_answers:
        parts.append(fallback_answers.strip())
    return "\n".join(p for p in parts if p)
