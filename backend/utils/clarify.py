"""
Clarify + extract pipeline, and the underlying LLM helpers it uses.

Public entrypoint (used by routers/intent_router.py):
    clarify_and_extract(text, prefs, persona, answers, agent_id, previous_questions)
        -> (clarification | None, final_text, extracted, embedding)

If clarification is not None, the caller should surface the questions to the
user and NOT persist anything. Otherwise the caller persists via utils.create.

Side effects this module performs directly (by design — stateful pipeline):
- Merges newly-inferred preferences / persona into the agent doc via `store`.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI

import store
from embeddings import embed
from geocode import geocode as geocode_location

MODEL = "gpt-5.4"  # NOTE: hallucinated; all LLM calls fall through to fallbacks until fixed
_openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

_LLM = Path(__file__).parent.parent / "llm"
_CLARIFY_PROMPT = (_LLM / "clarification.txt").read_text().strip()
_INTENT_PROMPT_TPL = (_LLM / "extract_intent.txt").read_text().strip()
_PREF_PROMPT = (_LLM / "extract_preference.txt").read_text().strip()
_PERSONA_PROMPT = (_LLM / "extract_persona.txt").read_text().strip()


async def _chat(system: str, user: str, json_mode: bool = False) -> str:
    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    resp = await _openai.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        **kw,
    )
    return (resp.choices[0].message.content or "").strip()


# --- Clarification ---------------------------------------------------------

async def check_clarification(text, preferences, persona=None, answers="", previous_questions=None):
    parts = [f'Intent: "{text}"']
    if persona:
        parts.append(f"Who they are: {persona}")
    if preferences:
        parts.append(f"Background: {preferences}")
    if previous_questions and answers:
        parts.append(f'Previous questions: "{" ".join(previous_questions)}"')
        parts.append(f'User answered: "{answers}"')
    elif answers:
        parts.append(f'User answered: "{answers}"')

    try:
        response = await _chat(_CLARIFY_PROMPT, "\n".join(parts))
    except Exception as e:
        print(f"[clarify] failed: {e}")
        return {"acknowledgements": [], "questions": []}

    if not response or response.lower().startswith("(empty"):
        return {"acknowledgements": [], "questions": []}

    acks, qs = [], []
    for line in response.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("(empty"):
            continue
        (qs if line.endswith("?") else acks).append(line)
    return {"acknowledgements": acks, "questions": qs}


# --- Structured intent / preferences / persona ----------------------------

_EXTRACT_DEFAULTS = {
    "domain": 15, "type": 1, "location_query": "", "radius": 10.0,
    "time_start": 0, "time_end": 0, "budget_min": 0, "budget_max": 0,
    "flags": 0, "tags": [],
}


async def extract_intent_structure(text, preferences):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    system = _INTENT_PROMPT_TPL.replace("{current_datetime}", now_str)
    parts = [f'Intent: "{text}"']
    if preferences:
        parts.append(f"Agent background: {preferences}")
    try:
        resp = await _chat(system, "\n".join(parts), json_mode=True)
        return {**_EXTRACT_DEFAULTS, **json.loads(resp)}
    except Exception as e:
        print(f"[extract_intent] failed: {e}")
        return dict(_EXTRACT_DEFAULTS)


async def extract_preferences(text) -> str:
    try:
        resp = await _chat(_PREF_PROMPT, text)
        return resp.strip()
    except Exception as e:
        print(f"[extract_pref] failed: {e}")
        return ""


async def extract_persona(text) -> str:
    try:
        resp = await _chat(_PERSONA_PROMPT, text)
        return resp.strip()
    except Exception as e:
        print(f"[extract_persona] failed: {e}")
        return ""


# --- Geocoding -------------------------------------------------------------

async def maybe_geocode(extracted):
    loc = extracted.get("location_query", "")
    if not loc or extracted.get("radius", 10.0) == 0.0:
        return extracted
    try:
        lat, lng = await geocode_location(loc)
        return {**extracted, "lat": lat, "lng": lng}
    except Exception as e:
        print(f"[geocode] failed for '{loc}': {e}")
        return extracted


# --- Agent mutations ------------------------------------------------------

async def save_preferences(agent_id: str, new_pref: str) -> None:
    if not new_pref:
        return
    now = datetime.now(timezone.utc).isoformat()

    def mutate(agent):
        current = agent.get("preferences") or ""
        agent["preferences"] = (current + "\n" + new_pref).strip() if current else new_pref
        agent["updated_at"] = now

    await store.update_agent(agent_id, mutate)


async def save_persona(agent_id: str, new_persona: str) -> None:
    if not new_persona:
        return
    now = datetime.now(timezone.utc).isoformat()

    def mutate(agent):
        current = agent.get("persona") or ""
        agent["persona"] = (current + "\n" + new_persona).strip() if current else new_persona
        agent["updated_at"] = now

    await store.update_agent(agent_id, mutate)


# --- Main pipeline ---------------------------------------------------------

async def clarify_and_extract(text, prefs, persona=None, answers="", agent_id="", previous_questions=None):
    clarification, new_prefs, new_persona = await asyncio.gather(
        check_clarification(text, prefs, persona, answers, previous_questions),
        extract_preferences(text),
        extract_persona(text),
    )

    if new_prefs and agent_id:
        await save_preferences(agent_id, new_prefs)
    if new_persona and agent_id:
        await save_persona(agent_id, new_persona)

    if clarification["questions"]:
        return clarification, None, None, None

    final_text = f"{text} — {answers}" if answers else text
    extracted, embedding = await asyncio.gather(
        extract_intent_structure(final_text, prefs),
        embed(final_text),
    )
    extracted = await maybe_geocode(extracted)
    return None, final_text, extracted, embedding
