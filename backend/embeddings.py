"""OpenAI text embeddings."""
import os

from openai import AsyncOpenAI

_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
_MODEL = "text-embedding-3-small"


async def embed(text: str) -> list[float]:
    resp = await _client.embeddings.create(model=_MODEL, input=text)
    return resp.data[0].embedding
