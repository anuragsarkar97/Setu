import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import APIRouter, Body, HTTPException

import faiss_index
from db import get_db
from embeddings import cosine_similarity, embed
from geocode import haversine_km

load_dotenv()

router = APIRouter(prefix="/api/matching", tags=["matching"])

# ---------------------------------------------------------------------------
# Backend toggle — flipped at runtime via POST /api/matching/config
# Initialised from env so it survives restarts without code change.
# ---------------------------------------------------------------------------
_USE_MONGO: bool = os.environ.get("USE_MONGO_MATCHING", "false").lower() == "true"


def _location_ok(src_ext: dict, cand_ext: dict) -> tuple[bool, float | None]:
    """
    Check whether a candidate passes the source's location filter.
    Returns (passes, distance_km).
    """
    src_lat = src_ext.get("lat")
    src_lng = src_ext.get("lng")
    src_radius = src_ext.get("radius") or 10.0

    is_remote = bool(cand_ext.get("flags", 0) & 2)
    if is_remote:
        return True, None
    if src_lat is None or src_lng is None:
        return True, None

    cand_lat = cand_ext.get("lat")
    cand_lng = cand_ext.get("lng")
    if cand_lat is None or cand_lng is None:
        return True, None

    dist = haversine_km(src_lat, src_lng, cand_lat, cand_lng)
    return dist <= src_radius, round(dist, 2)


async def _faiss_match(source, top_n: int, threshold: float) -> tuple[list, str]:
    """
    Use FAISS to find candidate intent_ids, then fetch full docs from MongoDB.
    Returns (results, index_used).
    """
    source_vec = source["embedding"]

    # Ask FAISS for top-50 candidates — we'll filter down after
    candidates_raw = faiss_index.search(source_vec, k=50)
    if not candidates_raw:
        return [], "faiss_empty"

    # Fetch full docs for candidates from MongoDB in one query
    candidate_ids = [iid for iid, _ in candidates_raw]
    score_map = {iid: score for iid, score in candidates_raw}

    cursor = get_db().intents.find(
        {
            "intent_id": {"$in": candidate_ids},
            "status": "active",
            "agent_id": {"$ne": source["agent_id"]},
        },
        {"_id": 0, "intent_id": 1, "agent_id": 1, "text": 1, "extracted": 1},
    )
    docs = await cursor.to_list(length=50)

    src_ext = source.get("extracted", {})
    scored = []
    for doc in docs:
        score = score_map.get(doc["intent_id"], 0.0)
        if score < threshold:
            continue

        cand_ext = doc.get("extracted", {})
        passes, dist_km = _location_ok(src_ext, cand_ext)
        if not passes:
            continue

        result = {
            "intent_id": doc["intent_id"],
            "agent_id": doc["agent_id"],
            "text": doc["text"],
            "score": round(score, 4),
            "domain": cand_ext.get("domain"),
            "tags": cand_ext.get("tags", []),
        }
        if dist_km is not None:
            result["distance_km"] = dist_km
        scored.append(result)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored, "faiss"


async def _mongo_match(source, top_n: int, threshold: float) -> tuple[list, str]:
    """
    Fallback: load all embeddings from MongoDB and compute cosine similarity.
    Used when the FAISS index is not ready yet.
    """
    cursor = get_db().intents.find(
        {
            "status": "active",
            "agent_id": {"$ne": source["agent_id"]},
            "embedding": {"$ne": []},
        },
        {"_id": 0, "intent_id": 1, "agent_id": 1, "text": 1, "embedding": 1, "extracted": 1},
    )
    candidates = await cursor.to_list(length=500)

    src_ext = source.get("extracted", {})
    source_vec = source["embedding"]
    scored = []

    for c in candidates:
        passes, dist_km = _location_ok(src_ext, c.get("extracted", {}))
        if not passes:
            continue
        score = cosine_similarity(source_vec, c["embedding"])
        if score < threshold:
            continue
        result = {
            "intent_id": c["intent_id"],
            "agent_id": c["agent_id"],
            "text": c["text"],
            "score": round(score, 4),
            "domain": c.get("extracted", {}).get("domain"),
            "tags": c.get("extracted", {}).get("tags", []),
        }
        if dist_km is not None:
            result["distance_km"] = dist_km
        scored.append(result)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored, "mongo_fallback"


# ---------------------------------------------------------------------------
# Public helper — used by intents.py search endpoint
# ---------------------------------------------------------------------------

async def run_match(source_doc: dict, top_n: int, threshold: float) -> tuple[list, str]:
    """
    Run matching against a source intent document (must have 'embedding' field).
    Returns (results, index_used). Does NOT write interaction logs — caller's responsibility.
    """
    if not _USE_MONGO and faiss_index.stats()["built"]:
        scored, index_used = await _faiss_match(source_doc, top_n, threshold)
    else:
        scored, index_used = await _mongo_match(source_doc, top_n, threshold)
    return scored[:top_n], index_used


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/match")
async def match_intent(body: dict = Body(...)):
    """
    Find the top N semantically similar intents.

    Body: { "intent_id": "...", "top_n": 5, "threshold": 0.7 }

    Uses FAISS for fast vector search when the index is ready.
    Falls back to a full MongoDB scan if not.
    Response includes "index_used": "faiss" | "mongo_fallback" so you can
    see which path was taken.
    """
    intent_id = body.get("intent_id")
    top_n = int(body.get("top_n", 5))
    threshold = float(body.get("threshold", 0.7))

    if not intent_id:
        raise HTTPException(status_code=400, detail="intent_id is required")

    source = await get_db().intents.find_one({"intent_id": intent_id})
    if not source:
        raise HTTPException(status_code=404, detail="Intent not found")
    if not source.get("embedding"):
        raise HTTPException(
            status_code=400,
            detail="Intent has no embedding — run POST /api/matching/embed/{intent_id} first",
        )

    if not _USE_MONGO and faiss_index.stats()["built"]:
        scored, index_used = await _faiss_match(source, top_n, threshold)
    else:
        scored, index_used = await _mongo_match(source, top_n, threshold)

    results = scored[:top_n]
    now = datetime.now(timezone.utc)

    # Log on source agent
    await get_db().agents.update_one(
        {"agent_id": source["agent_id"]},
        {"$push": {"interactions": {
            "event": "match_searched",
            "data": {
                "intent_id": intent_id,
                "matches_found": len(results),
                "top_score": results[0]["score"] if results else None,
                "index_used": index_used,
            },
            "timestamp": now,
        }}},
    )

    # Log on every matched agent so they know their intent was surfaced
    for match in results:
        await get_db().agents.update_one(
            {"agent_id": match["agent_id"]},
            {"$push": {"interactions": {
                "event": "intent_matched",
                "data": {
                    "matched_intent_id": match["intent_id"],
                    "source_intent_id": intent_id,
                    "source_agent_id": source["agent_id"],
                    "score": match["score"],
                },
                "timestamp": now,
            }}},
        )

    src_ext = source.get("extracted", {})
    return {
        "source_intent_id": intent_id,
        "source_text": source["text"],
        "source_location": src_ext.get("location_query", ""),
        "threshold": threshold,
        "index_used": index_used,
        "faiss_stats": faiss_index.stats(),
        "matches": results,
    }


@router.post("/build-index")
async def build_index():
    """
    Rebuild the FAISS index from all MongoDB intents.
    Call this after bulk imports or if the index gets stale.
    """
    count = await faiss_index.build(get_db())
    return {"status": "built", **faiss_index.stats()}


@router.get("/index-stats")
async def index_stats():
    """Current state of the FAISS index."""
    return faiss_index.stats()


@router.post("/embed/{intent_id}")
async def embed_intent(intent_id: str):
    """(Re)compute and store the embedding for a single intent."""
    intent = await get_db().intents.find_one({"intent_id": intent_id})
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    vec = await embed(intent["text"])
    await get_db().intents.update_one(
        {"intent_id": intent_id},
        {"$set": {"embedding": vec, "updated_at": datetime.now(timezone.utc)}},
    )
    faiss_index.add(intent_id, vec)
    return {"intent_id": intent_id, "dims": len(vec), "status": "embedded"}


@router.post("/embed-all")
async def embed_all_intents():
    """Backfill embeddings for all intents with empty embedding, then rebuild FAISS."""
    cursor = get_db().intents.find({"embedding": []}, {"_id": 0, "intent_id": 1, "text": 1})
    intents = await cursor.to_list(length=500)

    now = datetime.now(timezone.utc)
    count = 0
    for intent in intents:
        vec = await embed(intent["text"])
        await get_db().intents.update_one(
            {"intent_id": intent["intent_id"]},
            {"$set": {"embedding": vec, "updated_at": now}},
        )
        count += 1

    # Rebuild FAISS to include newly embedded intents
    total = await faiss_index.build(get_db())
    return {"embedded": count, "faiss_index_total": total}



@router.get("/config")
async def get_config():
    """Current matching backend configuration."""
    return {
        "use_mongo": _USE_MONGO,
        "backend": "mongodb" if _USE_MONGO else "faiss",
        "faiss": faiss_index.stats(),
    }


@router.post("/config")
async def set_config(body: dict = Body(...)):
    """
    Toggle the matching backend at runtime — no restart needed.
    Body: { "use_mongo": true } or { "use_mongo": false }

    use_mongo=true  → always use MongoDB full scan
    use_mongo=false → use FAISS (falls back to MongoDB if index not built)
    """
    global _USE_MONGO
    if "use_mongo" not in body:
        raise HTTPException(status_code=400, detail="use_mongo (bool) is required")
    _USE_MONGO = bool(body["use_mongo"])
    return {
        "updated": True,
        "use_mongo": _USE_MONGO,
        "backend": "mongodb" if _USE_MONGO else "faiss",
        "faiss": faiss_index.stats(),
    }
