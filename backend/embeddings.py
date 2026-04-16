"""
Embedding utilities using OpenAI text-embedding-3-small.
Client is initialized once at import time.
"""
import os

import numpy as np
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536


async def embed(text: str) -> list[float]:
    """Return a 1536-dim embedding for a text string via OpenAI."""
    resp = await _client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return resp.data[0].embedding


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Cosine similarity between two vectors.
    Returns a float in [-1, 1]. For text embeddings typically in [0, 1].
    """
    va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    norm = np.linalg.norm(va) * np.linalg.norm(vb)
    if norm == 0:
        return 0.0
    return float(np.dot(va, vb) / norm)
