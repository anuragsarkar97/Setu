"""
Chat loop with tool calling.

Conceptually this is an MCP-server-to-Claude shape, but using OpenAI's
function-calling API. The LLM drives the conversation and decides when to
invoke `search_intents` or `create_intent`. We persist the user/assistant
turns in the JSON store; tool messages are transient per request.

Public entrypoint:

    await run_chat(agent_id, user_message, conversation_id=None)
        -> {
             conversation_id,
             reply,                  # natural-language assistant reply
             tool_events,            # compact trace for the UI
             highlight_intent_ids,   # map-highlighting hints
           }
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI

import store
from utils.chat_tools import tool_create_intent, tool_search_intents
from utils.clarify import MODEL

_openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
_SYSTEM_PROMPT = (
    Path(__file__).parent.parent / "llm" / "chat_system_prompt.md"
).read_text().strip()

# Tool schemas (OpenAI function-calling shape) ------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_intents",
            "description": (
                "Search the bulletin for existing intents matching a natural-"
                "language query. Use when the user is looking, discovering, "
                "browsing, or comparing. Excludes the user's own intents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":     {"type": "string",  "description": "Natural-language search query."},
                    "top_n":     {"type": "integer", "description": "Max results. Default 5.", "default": 5},
                    "threshold": {"type": "number",  "description": "Min cosine similarity 0-1. Default 0.5.", "default": 0.5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_intent",
            "description": (
                "Post a new intent on behalf of the user. Use when the user is "
                "stating something that's theirs to post. May return "
                "'needs_clarification' with questions to ask — in that case, "
                "ask the user those questions, then call this tool again with "
                "the same `text`, plus `answers` and `previous_questions`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The user's intent as a single sentence.",
                    },
                    "answers": {
                        "type": "string",
                        "description": "The user's reply to a previous clarification, if any.",
                    },
                    "previous_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The questions asked on the previous clarification turn, if any.",
                    },
                },
                "required": ["text"],
            },
        },
    },
]

_TOOL_IMPLS = {
    "search_intents": tool_search_intents,
    "create_intent":  tool_create_intent,
}

_MAX_TOOL_LOOPS = 4  # safety rail against runaway tool-calling


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

        # Echo the assistant message (with tool_calls) back into the thread
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
                except TypeError as e:          # bad args shape
                    result = {"error": f"bad_args: {e}"}
                except Exception as e:
                    result = {"error": str(e)}

            # Collect map highlights
            if name == "search_intents" and isinstance(result.get("matches"), list):
                highlight_ids.extend(m.get("intent_id") for m in result["matches"] if m.get("intent_id"))
            if name == "create_intent" and result.get("status") == "created":
                iid = (result.get("intent") or {}).get("intent_id")
                if iid:
                    highlight_ids.append(iid)

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

    # Hit the loop budget — return gracefully
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
    """Slim version of the tool result for the UI trace (no embeddings, etc.)."""
    if not isinstance(result, dict):
        return {"value": str(result)[:200]}
    out = {}
    for k, v in result.items():
        if k in ("matches", "nearby_matches") and isinstance(v, list):
            out[k] = [
                {kk: m.get(kk) for kk in ("intent_id", "intent_type", "text", "score", "location", "tags") if kk in m}
                for m in v
            ]
        elif k == "intent" and isinstance(v, dict):
            out[k] = {kk: v.get(kk) for kk in ("intent_id", "intent_type", "summary", "location", "tags")}
        else:
            out[k] = v
    return out


def _dedup(ids):
    seen, out = set(), []
    for i in ids:
        if i and i not in seen:
            seen.add(i); out.append(i)
    return out
