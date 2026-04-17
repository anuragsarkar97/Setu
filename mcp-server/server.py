# Run:
#   pip install -e .
#   python server.py
#
# Or add to Claude Desktop claude_desktop_config.json:
# {
#   "mcpServers": {
#     "setu": {
#       "command": "python",
#       "args": ["/path/to/mcp-server/server.py"]
#     }
#   }
# }

import asyncio
import json
from mcp.server.fastmcp import FastMCP
from client import (
    clarify_intent,
    post_intent,
    search_intents,
    get_status,
    ack_match,
)

mcp = FastMCP("setu")


@mcp.tool()
async def intent_post(text: str) -> str:
    """Post an intent to Setu. Use this when the user wants to offer or request something.

    STEP 1 — ENRICH BEFORE CALLING:
    Before calling this tool, silently fold in everything you already know from the
    conversation — location, timing, budget, item details mentioned earlier.
    Build the richest possible one-sentence description first.
    Example: user says "selling my bike" but earlier said "I'm in Pune" →
    call with "selling my bike in Pune".

    STEP 2 — FILTER RETURNED QUESTIONS:
    If needs_clarification=true is returned with a list of questions, do NOT ask all
    of them blindly. First check each question against the conversation history:
    - If you already know the answer from context → silently fold it into the next call, do not ask
    - If it is genuinely unknown → ask the user

    Example: backend asks "Which area are you in?" but user said "I'm in Koramangala"
    two messages ago → skip that question, include Koramangala in the next call instead.

    You may also add your own clarifying questions if you notice something critical is
    missing that the backend didn't catch. Combine all questions into one message.

    CRITICAL RULES:
    1. This tool either posts OR asks for clarification. It never does both.
    2. Collect ALL outstanding answers before calling again — never call a second time
       after a partial answer.
    3. On the follow-up call, combine original intent + conversation context + all answers
       into one rich sentence.
    4. The intent is created only when needs_clarification=false is returned.

    Use setu_search if the user only wants to browse without posting.
    """
    try:
        # 1. Clarify first — never post until all critical fields are present
        questions = await clarify_intent(text)

        if questions:
            return json.dumps({
                "needs_clarification": True,
                "questions": questions,
                "posted": False,
                "instruction": (
                    "Ask the user ALL of these questions in one message. "
                    "Wait for ALL answers before calling setu_post again. "
                    "When you call again, combine the original intent + all answers into one natural sentence. "
                    "Do NOT call setu_post again until you have all the answers."
                ),
            })

        # 2. Intent is clear — post and search in parallel
        posted, searched = await asyncio.gather(
            post_intent(text),
            search_intents(text, limit=5),
        )

        return json.dumps({
            "needs_clarification": False,
            "intent_id": posted["id"],
            "text": posted["text"],
            "understood_as": {
                "domain": posted["domain"],
                "type": posted["type"],
                "time_start": posted["time_start"],
                "time_end": posted["time_end"],
                "budget_min": posted["budget_min"],
                "budget_max": posted["budget_max"],
            },
            "message": "Posted. Others can now find this intent via search.",
            "already_out_there": {
                "searched_for": searched["query"],
                "count": len(searched["results"]),
                "results": searched["results"],
            },
        })
    except Exception as e:
        return f"Failed to post intent: {e}"


@mcp.tool()
async def intent_search(
    query: str,
    limit: int = 10,
    lat: float = 0.0,
    lon: float = 0.0,
    radius_km: float = 30.0,
) -> str:
    """Search Setu for intents matching your query. Use natural language.

    Enrich the query from conversation context before calling — include location,
    timing, or budget the user already mentioned. A richer query returns better matches.

    Example: user asks "find a plumber" but earlier said "I'm in Koramangala" →
    search for "plumber in Koramangala" and pass lat/lon for Koramangala.

    LOCATION: Include the location in the query text ("find a plumber in Koramangala")
    and the backend will geocode it automatically. Only pass lat/lon explicitly if the
    user's location is known but not mentioned in the query itself.

    Returns a ranked list of matching intents. The search is semantic —
    it understands synonyms and context, not just keywords.

    Args:
        query: What you're looking for, in plain language — enriched with known context
        limit: Max number of results to return (default 10)
        lat: Searcher's latitude (0.0 = no location filter)
        lon: Searcher's longitude (0.0 = no location filter)
        radius_km: Search radius in km (default 30)
    """
    try:
        result = await search_intents(query, limit, lat, lon, radius_km)
        return json.dumps({
            "searched_for": result["query"],
            "results": result["results"],
            "count": len(result["results"]),
        })
    except Exception as e:
        return f"Failed to search: {e}"


@mcp.tool()
async def intent_status() -> str:
    """Check recent intents posted to Setu.

    Returns the most recent intents from all users.
    This is the discovery feed — browse what's out there.
    """
    try:
        data = await get_status()
        return json.dumps(data)
    except Exception as e:
        return f"Failed to get status: {e}"


@mcp.tool()
async def intent_ack(match_id: str) -> str:
    """Accept a match. If the other person also accepts, a chat room opens.

    Args:
        match_id: The match ID from setu_status

    Note: Match acknowledgement is not yet available — coming soon.
    """
    try:
        result = await ack_match(match_id)
        if result.get("status") == "chat_ready":
            room_id = result["room_id"]
            return json.dumps({
                "status": "chat_ready",
                "room_id": room_id,
                "message": f"Both accepted! Chat room {room_id} is open.",
            })
        return json.dumps({
            "status": "waiting_peer",
            "message": "You accepted. Waiting for the other person to accept.",
        })
    except NotImplementedError as e:
        return json.dumps({"status": "unavailable", "message": str(e)})
    except Exception as e:
        return f"Failed to ack match: {e}"


if __name__ == "__main__":
    mcp.run()
