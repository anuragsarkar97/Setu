"""
The ONE tool exposed to the chat LLM: `route_intent`.

Wraps the same code path as `POST /api/intent/route`. The chat LLM is not
responsible for deciding search vs create vs clarify vs respond — the
router handles all of that. The chat LLM's only job is to turn the
router's structured output into natural conversation.
"""
from fastapi import HTTPException

from routers.intent_router import route_intent as _route_handler


async def tool_route_intent(
    agent_id: str,
    text: str,
    answers: str = "",
    previous_questions: list | None = None,
) -> dict:
    """Invoke the intent router exactly like the HTTP endpoint does."""
    body = {
        "agent_id":           agent_id,
        "text":               (text or "").strip(),
        "answers":            (answers or "").strip(),
        "previous_questions": previous_questions or [],
    }
    try:
        return await _route_handler(body)
    except HTTPException as e:
        return {"error": e.detail, "status_code": e.status_code}
    except Exception as e:
        return {"error": str(e)}
