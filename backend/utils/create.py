"""Create a new intent document — persist, index, log."""
import uuid
from datetime import datetime, timezone

import faiss_index
from db import get_db


async def create_intent(
    agent_id: str,
    text: str,
    extracted: dict,
    embedding: list[float],
    prefs: dict,
) -> dict:
    """
    Persist a new intent, add its vector to FAISS, append an
    `intent_created` event to the owning agent's interaction log.
    Returns the stored document (without Mongo `_id`).
    """
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
