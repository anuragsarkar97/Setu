import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException

from db import ensure_indexes, get_db

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.post("", status_code=201)
async def create_agent(body: dict = Body(...)):
    """
    Create a new agent profile.
    Body: { "name": "Alice", "preferences": { "topic": "AI", ... } }
    """
    await ensure_indexes()
    now = datetime.now(timezone.utc)
    doc = {
        "agent_id": str(uuid.uuid4()),
        "name": body.get("name", "unnamed"),
        "preferences": body.get("preferences", {}),
        "persona": body.get("persona", {}),   # stable personal facts
        "history": [],
        "interactions": [],
        "neighbors": [],
        "created_at": now,
        "updated_at": now,
    }
    await get_db().agents.insert_one(doc)
    doc.pop("_id")
    return doc


@router.get("")
async def list_agents():
    """
    List all agent profiles — compact view, no history/interactions noise.
    """
    cursor = get_db().agents.find({}, {"_id": 0, "history": 0, "interactions": 0})
    return await cursor.to_list(length=200)


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """Return the full agent profile including history and interactions."""
    agent = await get_db().agents.find_one({"agent_id": agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.pop("_id")
    return agent


@router.patch("/{agent_id}/preferences")
async def update_preferences(agent_id: str, body: dict = Body(...)):
    """
    Merge-update preferences. Existing keys not in body are preserved.
    New merged state is snapshotted into history.
    Body: { "topic": "ML", "budget": "low" }
    """
    agent = await get_db().agents.find_one({"agent_id": agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    merged = {**agent["preferences"], **body}
    now = datetime.now(timezone.utc)

    await get_db().agents.update_one(
        {"agent_id": agent_id},
        {
            "$set": {"preferences": merged, "updated_at": now},
            "$push": {"history": {"updated_at": now, "snapshot": merged}},
        },
    )
    return {"agent_id": agent_id, "preferences": merged, "updated_at": now}


@router.patch("/{agent_id}/persona")
async def update_persona(agent_id: str, body: dict = Body(...)):
    """
    Merge-update persona. Existing keys not in body are preserved.
    Body: { "name": "Rahul", "pets": [{"name": "Max", "type": "dog", "breed": "Labrador"}], ... }
    Fields are merged at the top level — send only what changed.
    """
    agent = await get_db().agents.find_one({"agent_id": agent_id}, {"_id": 1})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    now = datetime.now(timezone.utc)
    set_payload = {f"persona.{k}": v for k, v in body.items()}
    set_payload["updated_at"] = now

    await get_db().agents.update_one({"agent_id": agent_id}, {"$set": set_payload})

    updated = await get_db().agents.find_one({"agent_id": agent_id}, {"_id": 0, "persona": 1})
    return {"agent_id": agent_id, "persona": updated.get("persona", {}), "updated_at": now}


@router.get("/{agent_id}/persona")
async def get_persona(agent_id: str):
    """Return the persona for an agent."""
    agent = await get_db().agents.find_one(
        {"agent_id": agent_id}, {"_id": 0, "agent_id": 1, "name": 1, "persona": 1}
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent["agent_id"], "name": agent["name"], "persona": agent.get("persona", {})}


@router.get("/{agent_id}/history")
async def get_preference_history(agent_id: str):
    """Preference evolution timeline — each entry is a full snapshot at that moment."""
    agent = await get_db().agents.find_one(
        {"agent_id": agent_id},
        {"_id": 0, "history": 1, "agent_id": 1, "name": 1},
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "agent_id": agent["agent_id"],
        "name": agent["name"],
        "preference_evolution": agent.get("history", []),
    }


@router.post("/{agent_id}/interactions", status_code=201)
async def log_interaction(agent_id: str, body: dict = Body(...)):
    """
    Append a generic interaction event.
    Body: { "event": "intent_submitted", "data": { ... } }
    Later phases write here automatically.
    """
    agent = await get_db().agents.find_one({"agent_id": agent_id}, {"_id": 1})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    entry = {
        "event": body.get("event", "unknown"),
        "data": body.get("data", {}),
        "timestamp": datetime.now(timezone.utc),
    }
    await get_db().agents.update_one(
        {"agent_id": agent_id},
        {"$push": {"interactions": entry}},
    )
    return entry


@router.get("/{agent_id}/interactions")
async def get_interactions(agent_id: str):
    """Full interaction log for an agent."""
    agent = await get_db().agents.find_one(
        {"agent_id": agent_id},
        {"_id": 0, "interactions": 1, "agent_id": 1, "name": 1},
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "agent_id": agent["agent_id"],
        "name": agent["name"],
        "interactions": agent.get("interactions", []),
    }


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str):
    """Delete an agent entirely. Useful for dev reset."""
    result = await get_db().agents.delete_one({"agent_id": agent_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
