"""
Single-file JSON-backed store.

- One file at /app/backend/data/store.json
- Top-level keys: "agents" and "intents" (both dict by id)
- Write-through: every mutation dumps the full file atomically
- asyncio.Lock serialises concurrent writes in a single-worker uvicorn
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_PATH = Path(__file__).parent / "data" / "store.json"
_LOCK = asyncio.Lock()
_DATA: dict[str, Any] = {"agents": {}, "intents": {}, "conversations": {}}
_LOADED = False


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not serialisable: {type(o)}")


def _load() -> None:
    global _DATA, _LOADED
    if _LOADED:
        return
    if _PATH.exists() and _PATH.stat().st_size > 0:
        _DATA = json.loads(_PATH.read_text())
    _DATA.setdefault("agents", {})
    _DATA.setdefault("intents", {})
    _DATA.setdefault("conversations", {})
    _LOADED = True


async def _flush() -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_DATA, indent=2, default=_default))
    tmp.replace(_PATH)  # atomic on POSIX


# --- Agents ----------------------------------------------------------------

def get_agent(agent_id: str) -> dict | None:
    _load()
    return _DATA["agents"].get(agent_id)


def list_agents() -> list[dict]:
    _load()
    return list(_DATA["agents"].values())


async def save_agent(agent: dict) -> None:
    _load()
    async with _LOCK:
        _DATA["agents"][agent["agent_id"]] = agent
        await _flush()


async def update_agent(agent_id: str, mutate: Callable[[dict], None]) -> dict | None:
    """Atomic read-modify-write on an agent. Returns the updated doc or None."""
    _load()
    async with _LOCK:
        agent = _DATA["agents"].get(agent_id)
        if not agent:
            return None
        mutate(agent)
        await _flush()
        return agent


# --- Intents ---------------------------------------------------------------

def all_intents() -> list[dict]:
    _load()
    return list(_DATA["intents"].values())


async def save_intent(intent: dict) -> None:
    _load()
    async with _LOCK:
        _DATA["intents"][intent["intent_id"]] = intent
        await _flush()


# --- Conversations ---------------------------------------------------------

def get_conversation(conversation_id: str) -> dict | None:
    _load()
    return _DATA["conversations"].get(conversation_id)


async def save_conversation(conv: dict) -> None:
    _load()
    async with _LOCK:
        _DATA["conversations"][conv["conversation_id"]] = conv
        await _flush()

