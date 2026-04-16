"""Search for intents semantically similar to a source intent."""
from routers.matching import run_match


async def search_matches(
    source_doc: dict,
    top_n: int = 5,
    threshold: float = 0.7,
) -> tuple[list, str]:
    """
    Returns (matches, index_used).
    `source_doc` must contain an `embedding` field (list[float]) and an
    `agent_id` (so the user's own intents are excluded).
    """
    return await run_match(source_doc, top_n, threshold)
