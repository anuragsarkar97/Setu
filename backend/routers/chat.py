"""Chat endpoint — single conversational surface over the intent tools."""
from fastapi import APIRouter, Body, HTTPException

import store
from utils.chat import run_chat

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("")
async def chat(body: dict = Body(...)):
    """
    Body:
      {
        "agent_id":        "...",   // required
        "message":         "...",   // required — the user's natural-language turn
        "conversation_id": "..."    // optional — continues an existing conversation
      }

    Returns:
      {
        "conversation_id":      "...",
        "reply":                "...",   // assistant's natural reply
        "tool_events":          [{tool, args, result}],
        "highlight_intent_ids": ["..."]
      }
    """
    agent_id = body.get("agent_id")
    message  = (body.get("message") or "").strip()
    conv_id  = body.get("conversation_id")

    if not agent_id:
        raise HTTPException(400, "agent_id is required")
    if not message:
        raise HTTPException(400, "message is required")
    if not store.get_agent(agent_id):
        raise HTTPException(404, "Agent not found")

    return await run_chat(agent_id, message, conv_id)


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Fetch a conversation's full turn history."""
    conv = store.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv
