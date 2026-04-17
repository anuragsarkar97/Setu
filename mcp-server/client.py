"""
Async HTTP client for the Setu MCP server.
Auth is optional — if no credentials file exists, requests are sent unauthenticated.
"""
import os
import httpx
from config import get_coordinator_url, CONFIG_PATH

BASE_URL = get_coordinator_url()


def _get_user_id() -> str:
    """Resolve user_id from env → config → fallback."""
    uid = os.environ.get("SETU_USER_ID", "").strip()
    if uid:
        return uid
    if CONFIG_PATH.exists():
        try:
            import json
            data = json.loads(CONFIG_PATH.read_text())
            uid = data.get("user_id", "").strip()
            if uid:
                return uid
        except Exception:
            pass
    return "anonymous"


async def _get_auth_headers() -> dict:
    """Return auth headers if credentials exist, else empty dict."""
    try:
        from auth import get_valid_token
        token = await get_valid_token()
        return {"Authorization": f"Bearer {token}"}
    except Exception:
        return {}


async def _request(method: str, path: str, body: dict = None) -> dict:
    """Make an HTTP request to the backend, with optional auth."""
    headers = await _get_auth_headers()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(path, headers=headers)
        else:
            resp = await client.request(method, path, json=body, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def clarify_intent(text: str) -> list[str]:
    """Return clarifying questions for a sparse intent, or [] if already sufficient."""
    result = await _request("POST", "/intent/clarification", {
        "user_id": _get_user_id(),
        "query": text,
    })
    return result.get("questions", [])


async def post_intent(text: str) -> dict:
    """Post a new intent. Returns the full IntentOut from the backend."""
    return await _request("POST", "/intent/", {
        "user_id": _get_user_id(),
        "query": text,
    })


async def search_intents(
    query: str,
    limit: int = 10,
    lat: float = 0.0,
    lon: float = 0.0,
    radius_km: float = 30.0,
) -> dict:
    """Search for intents matching a natural language query.

    Pass lat/lon to restrict results to a geographic radius.
    lat=0/lon=0 means no location filter (global results).
    """
    body: dict = {"query": query, "limit": limit}
    if lat != 0.0 or lon != 0.0:
        body["lat"] = lat
        body["lon"] = lon
        body["radius_km"] = radius_km
    return await _request("POST", "/intent/search", body)


async def get_status() -> dict:
    """Fetch recent intents from the feed (all users — no per-user filter yet)."""
    resp = await _request("GET", "/intent/?limit=20")
    return {
        "open_intents": resp.get("intents", []),
        "pending_matches": [],  # matches endpoint not yet implemented in backend
    }


async def ack_match(match_id: str) -> dict:
    """Accept a match — not yet implemented in the backend."""
    raise NotImplementedError("Match acknowledgement is not yet available.")


async def pass_match(match_id: str) -> dict:
    """Decline a match — not yet implemented in the backend."""
    raise NotImplementedError("Match passing is not yet available.")


async def smara_recall(query: str, intent_domain: str = None) -> dict:
    """Recall beliefs from Smara — not yet implemented in the backend."""
    raise NotImplementedError("Smara memory recall is not yet available.")
