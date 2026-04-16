"""Minimal agent CRUD — backed by the JSON store."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException

import store

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.post("", status_code=201)
async def create_agent(body: dict = Body(...)):
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "agent_id": str(uuid.uuid4()),
        "name": body.get("name", "unnamed"),
        "preferences": body.get("preferences", ""),
        "persona": body.get("persona", ""),
        "created_at": now,
        "updated_at": now,
    }
    await store.save_agent(doc)
    return doc


@router.get("")
async def list_agents():
    return store.list_agents()


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent
