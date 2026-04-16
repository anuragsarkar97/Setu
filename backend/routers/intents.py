import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Body, HTTPException, Query
from openai import AsyncOpenAI

import faiss_index
from db import get_db
from embeddings import embed
from geocode import geocode as geocode_location

load_dotenv()

router = APIRouter(prefix="/api/intents", tags=["intents"])

# --- LLM setup -----------------------------------------------------------

_openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
_MODEL = "gpt-5.4"
_LLM_DIR = Path(__file__).parent.parent / "llm"

_CLARIFICATION_SYSTEM_PROMPT = (_LLM_DIR / "clarification.txt").read_text().strip()
_EXTRACT_SYSTEM_PROMPT_TEMPLATE = (_LLM_DIR / "extract_intent.txt").read_text().strip()
_EXTRACT_PREFERENCE_PROMPT = (_LLM_DIR / "extract_preference.txt").read_text().strip()
_EXTRACT_PERSONA_PROMPT = (_LLM_DIR / "extract_persona.txt").read_text().strip()
# NOTE: these prompts are loaded once at startup — restart backend after editing .txt files


async def _chat(system: str, user: str) -> str:
    """Single wrapper around OpenAI chat completions."""
    resp = await _openai.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _format_prefs_for_prompt(prefs: dict) -> str:
    parts = []
    for k, v in prefs.items():
        if isinstance(v, dict):
            val = v.get("value", "")
            note = v.get("note", "")
            parts.append(f"{k}: {val}" + (f"  (context: {note})" if note else ""))
        else:
            parts.append(f"{k}: {v}")
    return ", ".join(parts)


def _format_persona_for_prompt(persona: dict) -> str:
    """Render persona as a compact human-readable string for LLM context."""
    if not persona:
        return ""
    parts = []
    if persona.get("name"):
        parts.append(f"name: {persona['name']}")
    if persona.get("pets"):
        pet_strs = [
            f"{p.get('name', 'pet')} ({p.get('breed', p.get('type', 'pet'))})"
            for p in persona["pets"]
        ]
        parts.append(f"pets: {', '.join(pet_strs)}")
    if persona.get("occupation"):
        occ = persona["occupation"]
        if isinstance(occ, dict):
            parts.append(f"occupation: {occ.get('role', '')} {occ.get('mode', '')}".strip())
        else:
            parts.append(f"occupation: {occ}")
    if persona.get("home"):
        home = persona["home"]
        if isinstance(home, dict):
            parts.append(f"home: {home.get('type', '')} in {home.get('area', '')}".strip())
        else:
            parts.append(f"home: {home}")
    if persona.get("dietary"):
        parts.append(f"dietary: {persona['dietary']}")
    if persona.get("family"):
        fam = persona["family"]
        parts.append(f"family: {fam.get('status', fam) if isinstance(fam, dict) else fam}")
    if persona.get("languages"):
        parts.append(f"languages: {', '.join(persona['languages'])}")
    if persona.get("skills"):
        parts.append(f"skills: {', '.join(persona['skills'])}")
    if persona.get("fitness"):
        parts.append(f"fitness: {persona['fitness']}")
    if persona.get("notes"):
        for k, v in persona["notes"].items():
            parts.append(f"{k}: {v}")
    return "; ".join(parts)


async def check_clarification(
    text: str,
    preferences: dict,
    persona: dict | None = None,
    answers: str = "",
    previous_questions: list[str] | None = None,
) -> dict:
    """
    Returns {"acknowledgements": [...], "questions": [...]}
    - acknowledgements: lines where Claude notes it's using a stored preference
    - questions: lines that end with "?" — actual things the user needs to answer
    Empty questions list means the intent is clear.

    On Pass 2, previous_questions is passed so Claude can map answers correctly.
    Persona (stable facts) is included so the LLM never asks for known facts.
    """
    context_parts = [f'INTENTS: "{text}"']
    if persona:
        persona_str = _format_persona_for_prompt(persona)
        if persona_str:
            context_parts.append(f"PERSONA: {persona_str}")
    if preferences:
        context_parts.append(f"Preferences: {_format_prefs_for_prompt(preferences)}")

    '''
    INTENTS: I have been working in the corporate world for long time, and now I am feeling a lot, lonely in my life.
    PERSONA: None 
    Preferences: None
    '''
    response = await _chat(_CLARIFICATION_SYSTEM_PROMPT, "\n".join(context_parts))

    if not response or response.lower().startswith("(empty"):
        return {"acknowledgements": [], "questions": []}

    acknowledgements, questions = [], []
    for line in response.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("(empty"):
            continue
        if line.endswith("?"):
            questions.append(line)
        else:
            acknowledgements.append(line)
    return {"acknowledgements": acknowledgements, "questions": questions}


async def extract_intent_structure(text: str, preferences: dict) -> dict:
    """
    Extract structured fields from a clear intent using Claude.
    Returns a dict matching the extract_intent.txt schema:
      domain, type, location_query, radius, time_start, time_end,
      budget_min, budget_max, flags, tags
    Falls back to safe defaults if parsing fails.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    system_prompt = _EXTRACT_SYSTEM_PROMPT_TEMPLATE.replace("{current_datetime}", now_str)

    context_parts = [f'Intent: "{text}"']
    if preferences:
        prefs_str = ", ".join(f"{k}: {v}" for k, v in preferences.items())
        context_parts.append(f"Agent background: {prefs_str}")

    response = await _chat(system_prompt, "\n".join(context_parts))
    response = (response or "").strip()

    _defaults = {
        "domain": 15, "type": 1, "location_query": "", "radius": 10.0,
        "time_start": 0, "time_end": 0, "budget_min": 0, "budget_max": 0,
        "flags": 0, "tags": [],
    }

    try:
        parsed = json.loads(response)
        # Merge with defaults so any missing keys are safe
        return {**_defaults, **parsed}
    except (json.JSONDecodeError, TypeError):
        print(f"[extract_intent] JSON parse failed: {repr(response)}")
        return _defaults


async def maybe_geocode(extracted: dict) -> dict:
    """
    If extracted has a non-empty location_query and is not remote (radius > 0),
    resolve it to lat/lng and add to the extracted dict.
    Fails silently — geocoding failure never blocks intent creation.
    """
    loc = extracted.get("location_query", "")
    if not loc or extracted.get("radius", 10.0) == 0.0:
        return extracted
    try:
        lat, lng = await geocode_location(loc)
        return {**extracted, "lat": lat, "lng": lng}
    except Exception as e:
        print(f"[geocode] failed for '{loc}': {e}")
        return extracted


async def extract_preferences_from_text(text: str) -> dict:
    """
    Extract lite preferences (location, availability, interests, etc.) from any text.
    Returns a dict of {key: {value, note}} — empty dict if nothing found.
    Always fails silently — preference extraction never blocks intent creation.
    """
    try:
        response = await _chat(_EXTRACT_PREFERENCE_PROMPT, text)
        response = (response or "").strip()
        parsed = json.loads(response)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        print(f"[extract_pref] failed: {e}")
        return {}


async def extract_persona_from_text(text: str) -> dict:
    """
    Extract stable personal facts from any text using the persona prompt.
    Returns a dict of persona keys — empty dict if nothing stable found.
    Always fails silently — persona extraction never blocks intent creation.
    """
    try:
        response = await _chat(_EXTRACT_PERSONA_PROMPT, text)
        response = (response or "").strip()
        parsed = json.loads(response)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        print(f"[extract_persona] failed: {e}")
        return {}


async def save_persona(agent_id: str, new_persona: dict) -> None:
    """
    Deep-merge new_persona into agent's persona using $set per-key.
    List fields (pets, languages, skills) are replaced entirely if provided.
    Unrelated keys are preserved.
    """
    if not new_persona:
        return
    set_payload = {f"persona.{k}": v for k, v in new_persona.items()}
    set_payload["updated_at"] = datetime.now(timezone.utc)
    await get_db().agents.update_one({"agent_id": agent_id}, {"$set": set_payload})
    print(f"[persona] saved {list(new_persona.keys())} for agent {agent_id[:8]}")


async def save_preferences(agent_id: str, new_prefs: dict) -> None:
    """
    Merge new_prefs into agent's preferences using $set.
    Each key is set individually so unrelated keys are preserved.
    Also pushes a history snapshot after the update.
    """
    if not new_prefs:
        return

    now = datetime.now(timezone.utc)

    # Attach updated_at to each new preference entry
    timestamped = {
        k: {**v, "updated_at": now} if isinstance(v, dict) else {"value": v, "updated_at": now}
        for k, v in new_prefs.items()
    }

    # Build $set payload: preferences.location = {...}, preferences.availability = {...}
    set_payload = {f"preferences.{k}": v for k, v in timestamped.items()}
    set_payload["updated_at"] = now

    # Fetch current preferences for history snapshot
    agent = await get_db().agents.find_one({"agent_id": agent_id}, {"preferences": 1})
    current_prefs = (agent or {}).get("preferences", {})
    merged = {**current_prefs, **timestamped}

    await get_db().agents.update_one(
        {"agent_id": agent_id},
        {
            "$set": set_payload,
            "$push": {"history": {"updated_at": now, "snapshot": merged}},
        },
    )
    print(f"[prefs] saved {list(new_prefs.keys())} for agent {agent_id[:8]}")


async def _run_pipeline(
    text: str, prefs: dict, persona: dict | None = None, answers: str = "",
    agent_id: str = "", previous_questions: list[str] | None = None,
) -> tuple[dict | None, str | None, dict | None, list | None]:
    """
    Returns (clarification, final_text, extracted, embedding).
    clarification is None when intent is clear, else {"acknowledgements":[], "questions":[]}.
    Runs preference + persona extraction in parallel with clarification check.
    """
    clarification_result, new_prefs, new_persona = await asyncio.gather(
        check_clarification(text, prefs, persona, answers, previous_questions),
        extract_preferences_from_text(text),
        extract_persona_from_text(text),
    )

    if new_prefs and agent_id:
        await save_preferences(agent_id, new_prefs)
    if new_persona and agent_id:
        await save_persona(agent_id, new_persona)

    if clarification_result["questions"]:
        return clarification_result, None, None, None

    final_text = f"{text} — {answers}" if answers else text

    extracted, embedding = await asyncio.gather(
        extract_intent_structure(final_text, prefs),
        embed(final_text),
    )
    extracted = await maybe_geocode(extracted)
    return None, final_text, extracted, embedding


# --- Routes ---------------------------------------------------------------

@router.post("")


@router.post("", status_code=201)
async def create_intent(body: dict = Body(...)):
    """
    Create an intent from natural language text.

    Pass 1 — intent may be vague:
      { "agent_id": "...", "text": "I want to sell my home" }
      → { "status": "needs_clarification", "questions": ["What price?", "Which city?"] }

    Pass 2 — user answers the questions:
      { "agent_id": "...", "text": "I want to sell my home", "answers": "50L, Bangalore" }
      → { "status": "created", "intent": { ... } }

    Claude uses the agent's existing preferences as background context
    so it doesn't ask for things the profile already reveals.
    """
    agent_id = body.get("agent_id")
    text = (body.get("text") or "").strip()
    answers = (body.get("answers") or "").strip()
    previous_questions = body.get("previous_questions") or []

    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    agent = await get_db().agents.find_one({"agent_id": agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    prefs = agent.get("preferences", {})
    persona = agent.get("persona", {})

    # Shared pipeline: clarification + pref/persona extraction → extract + embed → geocode
    clarification, final_text, extracted, embedding = await _run_pipeline(
        text, prefs, persona, answers, agent_id=agent_id, previous_questions=previous_questions
    )

    if clarification:
        return {
            "status": "needs_clarification",
            "acknowledgements": clarification["acknowledgements"],
            "questions": clarification["questions"],
            "hint": "Resubmit with 'answers' (your responses) and 'previous_questions' (the questions asked).",
        }

    now = datetime.now(timezone.utc)
    doc = {
        "intent_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "text": final_text,
        "extracted": extracted,
        "embedding": embedding,
        "status": "active",
        "preferences_snapshot": prefs,
        "created_at": now,
        "updated_at": now,
    }
    await get_db().intents.insert_one(doc)
    doc.pop("_id")
    faiss_index.add(doc["intent_id"], embedding)

    await get_db().agents.update_one(
        {"agent_id": agent_id},
        {"$push": {"interactions": {
            "event": "intent_created",
            "data": {"intent_id": doc["intent_id"], "text": doc["text"]},
            "timestamp": now,
        }}},
    )

    # Auto-search: find matches and return them alongside the created intent
    from routers.matching import run_match
    top_n = int(body.get("top_n", 5))
    threshold = float(body.get("threshold", 0.7))
    matches, index_used = await run_match(doc, top_n, threshold)

    return {
        "status": "created",
        "intent": doc,
        "matches": matches,
        "index_used": index_used,
    }


@router.get("")
async def list_intents(agent_id: str = Query(None)):
    """List intents. Filter by agent with ?agent_id=xxx."""
    query = {"agent_id": agent_id} if agent_id else {}
    cursor = get_db().intents.find(query, {"_id": 0})
    return await cursor.to_list(length=200)


@router.get("/{intent_id}")
async def get_intent(intent_id: str):
    """Return a single intent by id."""
    intent = await get_db().intents.find_one({"intent_id": intent_id}, {"_id": 0})
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    return intent


@router.post("/{intent_id}/regenerate")
async def regenerate_intent(intent_id: str):
    """
    Re-run clarification check on the stored intent text using the agent's
    CURRENT preferences. Useful after the agent's profile has evolved.

    - If clarification is still needed → returns questions (intent not updated).
    - If it passes → updates preferences_snapshot to current and returns the intent.
    """
    intent = await get_db().intents.find_one({"intent_id": intent_id})
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    agent = await get_db().agents.find_one({"agent_id": intent["agent_id"]})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    current_prefs = agent.get("preferences", {})
    current_persona = agent.get("persona", {})
    questions = await check_clarification(intent["text"], current_prefs, current_persona)

    if questions:
        return {
            "status": "needs_clarification",
            "questions": questions,
            "note": "Intent text is ambiguous even with updated preferences.",
        }

    now = datetime.now(timezone.utc)
    await get_db().intents.update_one(
        {"intent_id": intent_id},
        {"$set": {"preferences_snapshot": current_prefs, "updated_at": now}},
    )

    await get_db().agents.update_one(
        {"agent_id": intent["agent_id"]},
        {"$push": {"interactions": {
            "event": "intent_regenerated",
            "data": {"intent_id": intent_id},
            "timestamp": now,
        }}},
    )

    return {
        "status": "updated",
        "intent_id": intent_id,
        "text": intent["text"],
        "preferences_used": current_prefs,
        "updated_at": now,
    }
