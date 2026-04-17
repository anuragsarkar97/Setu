"""
Chat loop with a single tool: `route_intent`.

MCP-style layering:
    chat LLM  ←→  route_intent (tool)  →  intent router (orchestrator)

The chat LLM speaks to the user. It forwards the user's message to the
tool, which calls the intent router (the same code that powers
`POST /api/intent/route`). The router decides between clarify / create+search
/ respond, and the chat LLM turns that structured response into natural
conversation.

Public entrypoint:

    await run_chat(agent_id, user_message, conversation_id=None)
        -> {
             conversation_id,
             reply,
             tool_events,
             highlight_intent_ids,
           }
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI

import store
from utils.chat_tools import tool_route_intent
from utils.clarify import MODEL

_openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
_SYSTEM_PROMPT = (
    Path(__file__).parent.parent / "llm" / "chat_system_prompt.md"
).read_text().strip()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "route_intent",
            "description": (
                "Forward the user's message to the intent router. The router "
                "is a separate agent that decides whether to CLARIFY (ask the "
                "user more questions), CREATE AND SEARCH (post the intent and "
                "return matches), or RESPOND (conversational reply). Call this "
                "for every user message that isn't pure greeting / small talk. "
                "Pass the user's text verbatim — do NOT interpret or rewrite. "
                "If the router previously asked clarifying questions, include "
                "them in `previous_questions` and the user's reply in `answers`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The user's intent text, verbatim.",
                    },
                    "answers": {
                        "type": "string",
                        "description": "If the router asked clarification on the previous turn, the user's reply.",
                    },
                    "previous_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The clarification questions from the previous turn.",
                    },
                },
                "required": ["text"],
            },
        },
    },
]

_TOOL_IMPLS = {"route_intent": tool_route_intent}
_MAX_TOOL_LOOPS = 4


# ---------------------------------------------------------------------------

async def run_chat(
    agent_id: str,
    user_message: str,
    conversation_id: str | None = None,
) -> dict:
    conv = _load_or_new_conv(conversation_id, agent_id)
    conv["turns"].append({"role": "user", "content": user_message})

    messages = _seed_messages(conv, agent_id)
    tool_events: list[dict] = []
    highlight_ids: list[str] = []

    for _ in range(_MAX_TOOL_LOOPS):
        resp = await _openai.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            reply = (msg.content or "").strip() or "…"
            conv["turns"].append({"role": "assistant", "content": reply})
            conv["updated_at"] = datetime.now(timezone.utc).isoformat()
            await store.save_conversation(conv)
            return {
                "conversation_id":      conv["conversation_id"],
                "reply":                reply,
                "tool_events":          tool_events,
                "highlight_intent_ids": _dedup(highlight_ids),
            }

        messages.append({
            "role":    "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id":   tc.id,
                    "type": "function",
                    "function": {
                        "name":      tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                } for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            fn = _TOOL_IMPLS.get(name)
            if fn is None:
                result = {"error": f"unknown_tool:{name}"}
            else:
                try:
                    result = await fn(agent_id=agent_id, **args)
                except TypeError as e:
                    result = {"error": f"bad_args: {e}"}
                except Exception as e:
                    result = {"error": str(e)}

            # Collect map highlights based on the router's action
            if isinstance(result, dict):
                if result.get("action") == "created":
                    iid = (result.get("intent") or {}).get("intent_id")
                    if iid:
                        highlight_ids.append(iid)
                    for m in result.get("matches", []) or []:
                        if m.get("intent_id"):
                            highlight_ids.append(m["intent_id"])

            tool_events.append({
                "tool":   name,
                "args":   args,
                "result": _compact(result),
            })

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "name":         name,
                "content":      json.dumps(result, default=str),
            })

    fallback = "hmm — i got tangled trying that. can you say it again?"
    conv["turns"].append({"role": "assistant", "content": fallback})
    await store.save_conversation(conv)
    return {
        "conversation_id":      conv["conversation_id"],
        "reply":                fallback,
        "tool_events":          tool_events,
        "highlight_intent_ids": _dedup(highlight_ids),
    }


# --- helpers ---------------------------------------------------------------

def _load_or_new_conv(conversation_id, agent_id):
    if conversation_id:
        conv = store.get_conversation(conversation_id)
        if conv:
            return conv
    now = datetime.now(timezone.utc).isoformat()
    return {
        "conversation_id": conversation_id or str(uuid.uuid4()),
        "agent_id":        agent_id,
        "turns":           [],
        "created_at":      now,
        "updated_at":      now,
    }


def _seed_messages(conv: dict, agent_id: str) -> list:
    agent = store.get_agent(agent_id) or {}
    persona = (agent.get("persona") or "").strip()
    prefs   = (agent.get("preferences") or "").strip()

    sys = _SYSTEM_PROMPT
    ctx = []
    if persona: ctx.append(f"Persona: {persona}")
    if prefs:   ctx.append(f"Preferences: {prefs}")
    if ctx:
        sys += "\n\n## Agent context (read-only)\n" + "\n".join(ctx)

    out = [{"role": "system", "content": sys}]
    for t in conv["turns"]:
        if t.get("role") in ("user", "assistant") and t.get("content"):
            out.append({"role": t["role"], "content": t["content"]})
    return out


def _compact(result):
    """Slim tool-result payload for the UI (no embeddings, etc.)."""
    if not isinstance(result, dict):
        return {"value": str(result)[:200]}

    out = {}
    for k, v in result.items():
        if k == "matches" and isinstance(v, list):
            out[k] = [
                {kk: m.get(kk) for kk in ("intent_id", "intent_type", "text", "location", "tags", "score", "rerank_score") if kk in m}
                for m in v
            ]
        elif k == "intent" and isinstance(v, dict):
            out[k] = {kk: v.get(kk) for kk in ("intent_id", "intent_type", "summary", "location", "tags") if kk in v}
        elif k == "questions" and isinstance(v, list):
            out[k] = v
        else:
            out[k] = v
    return out


def _dedup(ids):
    seen, out = set(), []
    for i in ids:
        if i and i not in seen:
            seen.add(i); out.append(i)
    return out
