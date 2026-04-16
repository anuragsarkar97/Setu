"""
Vector search over a list of dicts that each carry an `embedding` field.

Single function: cosine_top_k(query_vec, candidates, k) → [(doc, score), ...]
Uses numpy for the math. Exact (no approximation).
"""
import numpy as np


def cosine_top_k(query_vec, candidates: list[dict], k: int = 5) -> list[tuple[dict, float]]:
    if not candidates:
        return []
    q = np.asarray(query_vec, dtype=np.float32)
    M = np.asarray([c["embedding"] for c in candidates], dtype=np.float32)

    q_norm = np.linalg.norm(q) or 1.0
    M_norm = np.linalg.norm(M, axis=1)
    M_norm[M_norm == 0] = 1.0

    sims = (M @ q) / (M_norm * q_norm)
    top = np.argsort(-sims)[:k]
    return [(candidates[i], float(sims[i])) for i in top]
