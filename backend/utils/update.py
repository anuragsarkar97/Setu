"""
Update an intent = write the new doc first, then delete the old one.

New-first ordering means if the second step fails, the agent still has
the intended state (the new intent). The caller can decide whether to
surface the partial failure.
"""
from fastapi import HTTPException

from utils.create import create_intent
from utils.delete import delete_intent


async def update_intent(
    agent_id: str,
    old_intent_id: str,
    text: str,
    extracted: dict,
    embedding: list[float],
    prefs: dict,
) -> tuple[dict, str | None]:
    """
    Returns (new_doc, replaced_intent_id).
    replaced_intent_id is None if the old doc was already gone.
    """
    new_doc = await create_intent(agent_id, text, extracted, embedding, prefs)

    try:
        await delete_intent(old_intent_id, agent_id)
        replaced_id: str | None = old_intent_id
    except HTTPException:
        replaced_id = None

    return new_doc, replaced_id
