"""Semantic search over stored intents."""
import store
from embeddings import embed
from geocode import haversine_km
from vector_search import cosine_top_k


def _location_ok(src_ext: dict, cand_ext: dict):
    if bool(cand_ext.get("flags", 0) & 2):           # candidate flagged remote
        return True, None
    src_lat, src_lng = src_ext.get("lat"), src_ext.get("lng")
    if src_lat is None or src_lng is None:
        return True, None
    cand_lat, cand_lng = cand_ext.get("lat"), cand_ext.get("lng")
    if cand_lat is None or cand_lng is None:
        return True, None
    dist = haversine_km(src_lat, src_lng, cand_lat, cand_lng)
    return dist <= (src_ext.get("radius") or 10.0), round(dist, 2)


def _fmt(doc: dict, score: float, dist: float | None = None) -> dict:
    out = {
        "intent_id": doc["intent_id"],
        "agent_id": doc["agent_id"],
        "text": doc["text"],
        "score": round(float(score), 4),
        "domain": doc.get("extracted", {}).get("domain"),
        "tags": doc.get("extracted", {}).get("tags", []),
    }
    if dist is not None:
        out["distance_km"] = dist
    return out


def search_by_vector(query_vec, source_extracted: dict, exclude_agent_id: str, top_n: int = 5, threshold: float = 0.7):
    candidates = [
        i for i in store.all_intents()
        if i.get("status") == "active"
        and i.get("agent_id") != exclude_agent_id
        and i.get("embedding")
    ]
    top = cosine_top_k(query_vec, candidates, k=max(top_n * 3, 20))
    results = []
    for doc, score in top:
        if score < threshold:
            continue
        ok, dist = _location_ok(source_extracted, doc.get("extracted", {}))
        if not ok:
            continue
        results.append(_fmt(doc, score, dist))
        if len(results) >= top_n:
            break
    return results


async def search_by_text(query_text: str, exclude_agent_id: str, top_n: int = 5, threshold: float = 0.7):
    vec = await embed(query_text)
    return search_by_vector(vec, {}, exclude_agent_id, top_n, threshold)
