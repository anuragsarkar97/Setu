from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException

from db import get_db
from embeddings import cosine_similarity

router = APIRouter(prefix="/api/routing", tags=["routing"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _agent_name(agent_id: str) -> str:
    """Fetch agent name for readable path labels. Falls back to short ID."""
    a = await get_db().agents.find_one({"agent_id": agent_id}, {"name": 1})
    return (a or {}).get("name", agent_id[:8])


async def _match_at_agent(
    source_vec: list, target_agent_id: str, threshold: float
) -> dict | None:
    """
    Find the best-matching intent among target_agent_id's active intents.
    Returns the top match dict or None if nothing clears the threshold.
    This is what each 'node' in the distributed system would do locally.
    """
    cursor = get_db().intents.find(
        {"agent_id": target_agent_id, "status": "active", "embedding": {"$ne": []}},
        {"_id": 0, "intent_id": 1, "agent_id": 1, "text": 1, "embedding": 1},
    )
    intents = await cursor.to_list(length=100)

    best, best_score = None, -1.0
    for intent in intents:
        score = cosine_similarity(source_vec, intent["embedding"])
        if score >= threshold and score > best_score:
            best_score = score
            best = {
                "intent_id": intent["intent_id"],
                "agent_id": intent["agent_id"],
                "text": intent["text"],
                "score": round(score, 4),
            }
    return best


# ---------------------------------------------------------------------------
# Neighbor management
# ---------------------------------------------------------------------------

@router.post("/connect", status_code=201)
async def connect_agents(body: dict = Body(...)):
    """
    Create a bidirectional edge between two agents.
    Body: { "agent_id_a": "...", "agent_id_b": "..." }
    If the edge already exists it is a no-op (addToSet).
    """
    a = body.get("agent_id_a")
    b = body.get("agent_id_b")
    if not a or not b:
        raise HTTPException(status_code=400, detail="agent_id_a and agent_id_b are required")
    if a == b:
        raise HTTPException(status_code=400, detail="An agent cannot neighbor itself")

    for agent_id in (a, b):
        exists = await get_db().agents.find_one({"agent_id": agent_id}, {"_id": 1})
        if not exists:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    # $addToSet is idempotent — safe to call multiple times
    await get_db().agents.update_one({"agent_id": a}, {"$addToSet": {"neighbors": b}})
    await get_db().agents.update_one({"agent_id": b}, {"$addToSet": {"neighbors": a}})

    name_a, name_b = await _agent_name(a), await _agent_name(b)
    now = datetime.now(timezone.utc)

    # Log on both agents
    for agent_id, other_name in ((a, name_b), (b, name_a)):
        await get_db().agents.update_one(
            {"agent_id": agent_id},
            {"$push": {"interactions": {
                "event": "neighbor_connected",
                "data": {"neighbor_id": b if agent_id == a else a, "neighbor_name": other_name},
                "timestamp": now,
            }}},
        )

    return {"connected": True, "edge": f"{name_a} ↔ {name_b}"}


@router.delete("/connect")
async def disconnect_agents(body: dict = Body(...)):
    """
    Remove a bidirectional edge between two agents.
    Body: { "agent_id_a": "...", "agent_id_b": "..." }
    """
    a = body.get("agent_id_a")
    b = body.get("agent_id_b")
    if not a or not b:
        raise HTTPException(status_code=400, detail="agent_id_a and agent_id_b are required")

    await get_db().agents.update_one({"agent_id": a}, {"$pull": {"neighbors": b}})
    await get_db().agents.update_one({"agent_id": b}, {"$pull": {"neighbors": a}})
    return {"disconnected": True}


@router.get("/neighbors/{agent_id}")
async def get_neighbors(agent_id: str):
    """Return the neighbor list for an agent with names for readability."""
    agent = await get_db().agents.find_one(
        {"agent_id": agent_id}, {"_id": 0, "agent_id": 1, "name": 1, "neighbors": 1}
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    neighbor_ids = agent.get("neighbors") or []
    neighbors = []
    for nid in neighbor_ids:
        name = await _agent_name(nid)
        neighbors.append({"agent_id": nid, "name": name})

    return {
        "agent_id": agent_id,
        "name": agent.get("name"),
        "neighbor_count": len(neighbors),
        "neighbors": neighbors,
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@router.post("/route")
async def route_intent(body: dict = Body(...)):
    """
    Forward an intent through the agent graph until a match is found.

    Body: { "intent_id": "...", "threshold": 0.7, "max_hops": 3 }

    Algorithm: BFS from the source agent outward through neighbor edges.
    Each agent checks its OWN intents — simulating what a real distributed
    node would do: receive a forwarded message, scan local data, reply or
    forward on.

    Returns path taken as readable labels:
      "Alice → Bob → Carol → MATCH"  (if matched)
      "Alice → Bob → Carol → ..."    (if exhausted)
    """
    intent_id = body.get("intent_id")
    threshold = float(body.get("threshold", 0.7))
    max_hops = int(body.get("max_hops", 3))

    if not intent_id:
        raise HTTPException(status_code=400, detail="intent_id is required")

    intent = await get_db().intents.find_one({"intent_id": intent_id})
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    source_vec = intent.get("embedding", [])
    if not source_vec:
        raise HTTPException(
            status_code=400,
            detail="Intent has no embedding — run POST /api/matching/embed/{intent_id} first",
        )

    source_agent_id = intent["agent_id"]

    # --- BFS ---
    visited = {source_agent_id}
    current_level = [source_agent_id]   # agents to expand this round
    path_ids = [source_agent_id]        # all agents touched, in visit order

    match_result = None
    hops_taken = 0

    for hop in range(1, max_hops + 1):
        next_level = []

        for current_agent_id in current_level:
            agent = await get_db().agents.find_one(
                {"agent_id": current_agent_id}, {"neighbors": 1}
            )
            neighbors = (agent or {}).get("neighbors") or []

            for neighbor_id in neighbors:
                if neighbor_id in visited:
                    continue

                visited.add(neighbor_id)
                path_ids.append(neighbor_id)

                # Each neighbor does its own local match — the core of the simulation
                match = await _match_at_agent(source_vec, neighbor_id, threshold)
                if match:
                    match_result = match
                    hops_taken = hop

                    # Log on the matched agent's side
                    await get_db().agents.update_one(
                        {"agent_id": neighbor_id},
                        {"$push": {"interactions": {
                            "event": "intent_matched_via_routing",
                            "data": {
                                "matched_intent_id": match["intent_id"],
                                "source_intent_id": intent_id,
                                "source_agent_id": source_agent_id,
                                "score": match["score"],
                                "hops": hop,
                            },
                            "timestamp": datetime.now(timezone.utc),
                        }}},
                    )
                    break

                next_level.append(neighbor_id)

            if match_result:
                break

        if match_result:
            break

        current_level = next_level
        if not current_level:
            break  # graph exhausted

    # Build human-readable path labels
    path_names = [await _agent_name(aid) for aid in path_ids]

    if match_result:
        matched_name = await _agent_name(match_result["agent_id"])
        path_display = " → ".join(path_names) + f" → MATCH ({matched_name})"
    else:
        path_display = " → ".join(path_names) + " → (no match)"

    # Log routing attempt on the source agent's interaction history
    await get_db().agents.update_one(
        {"agent_id": source_agent_id},
        {"$push": {"interactions": {
            "event": "routing_attempted",
            "data": {
                "intent_id": intent_id,
                "status": "matched" if match_result else "no_match",
                "hops": hops_taken,
                "agents_visited": len(path_ids),
                "path_display": path_display,
            },
            "timestamp": datetime.now(timezone.utc),
        }}},
    )

    return {
        "status": "matched" if match_result else "no_match",
        "path_display": path_display,
        "path": path_ids,
        "hops": hops_taken,
        "agents_visited": len(path_ids),
        "threshold": threshold,
        "match": match_result,
    }
