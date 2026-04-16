"""Delete an intent document owned by a given agent."""
from datetime import datetime, timezone

from fastapi import HTTPException

from db import get_db


async def delete_intent(intent_id: str, agent_id: str) -> dict:
    """
    Delete an intent. Scoped by agent_id so one agent can't erase another's
    intents by passing a stolen id. Appends an `intent_deleted` event to
    the agent's interaction log.
    Raises 404 if no such intent exists for this agent.
    """
    result = await get_db().intents.delete_one(
        {"intent_id": intent_id, "agent_id": agent_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=404, detail=f"Intent not found: {intent_id}"
        )

    await get_db().agents.update_one(
        {"agent_id": agent_id},
        {"$push": {"interactions": {
            "event": "intent_deleted",
            "data": {"intent_id": intent_id},
            "timestamp": datetime.now(timezone.utc),
        }}},
    )
    return {"intent_id": intent_id, "deleted": True}
