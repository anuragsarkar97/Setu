"""
LLM helpers shared across the intent pipeline.

Exports used externally:
  MODEL                    — model name for all LLM calls
  extract_intent_structure — fallback structured extraction when router enriched_intent is absent
  save_preferences         — append inferred preferences to agent doc
  save_persona             — append inferred persona to agent doc
"""
import json
import os
from datetime import datetime, timezone

from openai import AsyncOpenAI

import store

MODEL = "gpt-5.4"
_openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

_EXTRACT_DEFAULTS = {
    "domain": 15, "type": 1, "location_query": "", "radius": 10.0,
    "time_start": 0, "time_end": 0, "budget_min": 0, "budget_max": 0,
    "flags": 0, "tags": [],
}

_INTENT_PROMPT_TPL = """You extract structured fields from a natural language intent.
Current datetime: {current_datetime}

Return JSON with these fields (use null / 0 / "" for unknowns):
- location_query: string  — city or neighbourhood for geocoding, empty string if absent
- radius: number          — search radius in km, default 10
- budget_min: number      — minimum budget, 0 if absent
- budget_max: number      — maximum budget, 0 if absent
- time_start: number      — unix timestamp of earliest acceptable time, 0 if absent
- time_end: number        — unix timestamp of latest acceptable time, 0 if absent
- flags: number           — bitmask: 1=urgent, 2=remote_ok; 0 if none apply
- tags: [string]          — 2-5 short descriptive tags
- domain: number          — best-fit domain (1=housing,2=dating,3=hiring,4=buying,5=selling,6=activity,7=community,15=other)
- type: number            — intent direction (1=seeking,2=offering,3=both)"""


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


async def extract_intent_structure(text: str, preferences: str) -> dict:
    """Fallback structured extraction — used when router enriched_intent is absent."""
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


_PERSONA_REFRESH_PROMPT = """\
You maintain a concise persona profile for a user of Sangam — an anonymous people-matching platform.

Given the user's CURRENT PERSONA and a NEW INTENT they just posted, produce an UPDATED PERSONA.

Rules:
1. Merge, don't append. Never repeat a fact already in the current persona.
2. Capture stable facts: home city/neighbourhood, lifestyle (dietary, smoking), recurring interests,
   typical budget range, work style, domains they frequently engage in.
3. If the new intent reveals new stable info not in the current persona, add it.
4. If the new intent confirms existing info, keep it unchanged — do not duplicate.
5. If the new intent contradicts existing info (e.g. different city), update to the newer value.
6. Drop ephemeral details: urgency, specific one-off dates, transient locations ("near me").
7. Output 1–3 concise sentences. Plain text only. No headers, no bullets, no JSON.
8. If the current persona is empty, derive a fresh persona from the new intent alone.
9. If the intent reveals nothing new or stable, return the current persona unchanged.

Examples of what to capture:
  "Lives and works in Koramangala, Bangalore. Vegetarian and non-smoker. Typically looks for flatmates in the 12–15k/month range."
  "Based in HSR Layout, Bangalore. Works from home. Interested in weekend hikes and tech community events."
"""


async def refresh_persona(agent_id: str, intent_text: str, extracted: dict) -> None:
    """Synthesize current persona + new intent into an updated persona and persist it."""
    agent = store.get_agent(agent_id)
    if not agent:
        return

    current_persona = (agent.get("persona") or "").strip()

    intent_summary = {
        "text": intent_text[:400],
        "intent_type": extracted.get("intent_type", ""),
        "location": extracted.get("location_query", ""),
        "budget_min": extracted.get("budget_min") or None,
        "budget_max": extracted.get("budget_max") or None,
        "dietary": extracted.get("dietary"),
        "smoking": extracted.get("smoking"),
        "tags": (extracted.get("tags") or [])[:5],
    }

    user_msg = json.dumps({
        "current_persona": current_persona or None,
        "new_intent": intent_summary,
    }, ensure_ascii=False)

    try:
        updated = await _chat(_PERSONA_REFRESH_PROMPT, user_msg)
        updated = updated.strip()
    except Exception as e:
        print(f"[refresh_persona] LLM failed: {e}")
        return

    if not updated or updated == current_persona:
        return

    now = datetime.now(timezone.utc).isoformat()

    def mutate(a):
        a["persona"] = updated
        a["updated_at"] = now

    await store.update_agent(agent_id, mutate)
    print(f"[persona] updated for {agent_id}: {updated[:80]}...")
