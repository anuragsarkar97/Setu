"""
In-memory FAISS index for fast intent similarity search.

MongoDB is still the source of truth.
FAISS is a read-accelerator — it finds candidate intent_ids quickly.
MongoDB then does the authoritative filter (status, agent, location).

Index type: IndexFlatIP (inner product on L2-normalised vectors = cosine similarity).
This gives EXACT results, not approximate — no accuracy trade-off.
"""
import numpy as np

try:
    import faiss
    _FAISS_AVAILABLE = True
except ImportError:
    _FAISS_AVAILABLE = False

DIMS = 1536  # text-embedding-3-small

# Module-level state — one index per process
_index = None           # faiss.IndexFlatIP
_id_map: list[str] = [] # position → intent_id
_built = False


def _is_ready() -> bool:
    return _FAISS_AVAILABLE and _built and _index is not None and _index.ntotal > 0


def _norm(vec: list[float]) -> np.ndarray:
    """Return a float32 row-matrix with L2-normalised vector."""
    v = np.array([vec], dtype=np.float32)
    faiss.normalize_L2(v)
    return v


async def build(db) -> int:
    """
    Load all embeddings from MongoDB and build the FAISS index from scratch.
    Safe to call multiple times — completely replaces the previous index.
    Returns the number of vectors indexed.
    """
    global _index, _id_map, _built

    if not _FAISS_AVAILABLE:
        print("[faiss] faiss-cpu not installed — skipping index build")
        return 0

    cursor = db.intents.find(
        {"embedding": {"$ne": []}},
        {"_id": 0, "intent_id": 1, "embedding": 1},
    )
    intents = await cursor.to_list(length=50_000)

    _index = faiss.IndexFlatIP(DIMS)
    _id_map = []

    if not intents:
        _built = True
        print("[faiss] index built — 0 vectors (no intents with embeddings yet)")
        return 0

    vectors = np.array([i["embedding"] for i in intents], dtype=np.float32)
    faiss.normalize_L2(vectors)
    _index.add(vectors)
    _id_map = [i["intent_id"] for i in intents]
    _built = True

    print(f"[faiss] index built — {len(_id_map)} vectors, dims={DIMS}")
    return len(_id_map)


def add(intent_id: str, embedding: list[float]) -> None:
    """
    Add a single new vector to the index.
    Called immediately after a new intent is created.
    """
    global _index, _id_map

    if not _FAISS_AVAILABLE or _index is None:
        return

    _index.add(_norm(embedding))
    _id_map.append(intent_id)


def search(query_vec: list[float], k: int = 50) -> list[tuple[str, float]]:
    """
    Return up to k (intent_id, cosine_score) pairs, sorted by score desc.
    Scores are in [-1, 1] — for normalised text embeddings typically [0, 1].
    Returns empty list if index is not ready.
    """
    if not _is_ready():
        return []

    k = min(k, _index.ntotal)
    scores, indices = _index.search(_norm(query_vec), k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        results.append((_id_map[idx], float(score)))

    return results  # already sorted by FAISS (highest first)


def stats() -> dict:
    return {
        "available": _FAISS_AVAILABLE,
        "built": _built,
        "total_vectors": _index.ntotal if _index else 0,
        "dims": DIMS,
    }
