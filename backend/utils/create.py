"""Persist a new intent to the JSON store."""
import uuid
from datetime import datetime, timezone

import store


async def create_intent(agent_id: str, text: str, extracted: dict, embedding: list[float], prefs: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
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
    await store.save_intent(doc)
    return doc
