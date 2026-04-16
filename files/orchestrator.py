from models import (
    Action, EnrichedIntent, HardFilter, IntentThread,
    OrchestratorOutput, Persona, SearchResult
)


def _extract_signals(thread: IntentThread, persona: Persona | None) -> dict:
    """Pull known signals from thread text + persona."""
    text = thread.full_text().lower()
    signals = {}

    # From persona
    if persona:
        if persona.location: signals["location"] = persona.location
        if persona.dietary:  signals["dietary"]  = persona.dietary
        if persona.smoking:  signals["smoking"]  = persona.smoking

    # From thread text (simple keyword extraction — LLM does this properly later)
    for loc in ["koramangala", "indiranagar", "hsr", "whitefield", "baner", "pune", "bangalore"]:
        if loc in text:
            signals.setdefault("location", loc.title())

    for kw in ["vegetarian", "veg "]:
        if kw in text:
            signals.setdefault("dietary", "vegetarian")

    for kw in ["non-veg", "nonveg", "non veg"]:
        if kw in text:
            signals.setdefault("dietary", "non-vegetarian")

    for kw in ["non-smoker", "no smoking", "non smoker"]:
        if kw in text:
            signals.setdefault("smoking", "non-smoker")

    # Budget — look for numbers near k
    import re
    budget_matches = re.findall(r'(\d+)\s*(?:k|thousand|,000)', text)
    if budget_matches:
        amounts = [int(x) * 1000 for x in budget_matches]
        signals["budget_min"] = min(amounts)
        signals["budget_max"] = max(amounts)

    # Timeline
    for kw in ["january", "jan", "february", "feb", "end of month", "asap", "tonight"]:
        if kw in text:
            signals.setdefault("timeline", kw)
            break

    return signals


def _already_asked(thread: IntentThread, topic: str) -> bool:
    asked = " ".join(thread.questions_asked()).lower()
    return topic.lower() in asked


def orchestrate(
    thread:         IntentThread,
    persona:        Persona | None   = None,
    search_result:  SearchResult | None = None,
) -> OrchestratorOutput:

    signals  = _extract_signals(thread, persona)
    n_turns  = sum(1 for m in thread.messages if m.type == "question")

    # ── POST-SEARCH decision ──────────────────────────────────────────
    if search_result is not None:
        top = search_result.top_score
        dist = search_result.score_distribution

        if top >= 0.80 or n_turns >= 2:
            return OrchestratorOutput(
                reasoning="Top score is strong and results are ready to serve.",
                action=Action.SERVE,
            )

        if dist == "spread" and n_turns < 2:
            return OrchestratorOutput(
                reasoning="Results are spread across clusters — one targeted question will sharpen ranking.",
                action=Action.ASK,
                question="I found a few options — are you looking for a single room or a shared room?",
            )

        return OrchestratorOutput(
            reasoning="Results are acceptable. Serving now.",
            action=Action.SERVE,
        )

    # ── PRE-SEARCH decision ───────────────────────────────────────────

    # Hard stop — never clarify after 3 turns
    if n_turns >= 3:
        return _build_search(signals, "Reached max clarification turns. Searching with available signals.")

    has_location = "location" in signals
    has_budget   = "budget_max" in signals
    has_lifestyle = "dietary" in signals or "smoking" in signals

    # No location at all — most critical eliminating signal
    if not has_location and not _already_asked(thread, "location"):
        return OrchestratorOutput(
            reasoning="Location is missing — it's the primary eliminating signal. Without it retrieval returns candidates from everywhere.",
            action=Action.ASK,
            question="Which area or neighbourhood are you looking in?",
        )

    # Have location, missing budget
    if not has_budget and not _already_asked(thread, "budget"):
        return OrchestratorOutput(
            reasoning="Location is known but budget is missing. Budget eliminates a large portion of the pool.",
            action=Action.ASK,
            question="What's your budget range and when do you need to move in?",
        )

    # Have location + budget, missing lifestyle
    if not has_lifestyle and not _already_asked(thread, "vegetarian"):
        return OrchestratorOutput(
            reasoning="Core filters are present. Lifestyle preferences (diet, smoking) will significantly reorder the remaining pool.",
            action=Action.ASK,
            question="Any lifestyle preferences — vegetarian or non-veg, smoker or non-smoker?",
        )

    # Enough signal — search
    return _build_search(signals, "All core eliminating signals are present. Completeness is sufficient to search.")


def _build_search(signals: dict, reasoning: str) -> OrchestratorOutput:
    hf = HardFilter(
        location   = signals.get("location"),
        budget_min = signals.get("budget_min"),
        budget_max = signals.get("budget_max"),
        timeline   = signals.get("timeline"),
        dietary    = signals.get("dietary"),
        smoking    = signals.get("smoking"),
        urgency    = False,
    )

    soft = []
    if signals.get("dietary"):    soft.append(f"{signals['dietary']} household preferred")
    if signals.get("smoking"):    soft.append("non-smoking environment")
    if signals.get("location"):   soft.append(f"looking in {signals['location']}")

    query_parts = ["Looking for a flatmate"]
    if hf.location:   query_parts.append(f"in {hf.location}")
    if hf.budget_max: query_parts.append(f"budget up to {int(hf.budget_max):,}")
    if hf.timeline:   query_parts.append(f"move-in around {hf.timeline}")
    if hf.dietary:    query_parts.append(hf.dietary)
    if hf.smoking:    query_parts.append(hf.smoking)

    known = len([v for v in [hf.location, hf.budget_max, hf.dietary, hf.smoking] if v])
    completeness = round(0.4 + known * 0.15, 2)

    return OrchestratorOutput(
        reasoning=reasoning,
        action=Action.SEARCH,
        enriched_intent=EnrichedIntent(
            intent_type     = "flatmate",
            summary         = " ".join(query_parts) + ".",
            hard_filters    = hf,
            soft_signals    = soft,
            embedding_query = " ".join(query_parts) + ". Seeking compatible, comfortable living arrangement.",
            missing_signals = [
                s for s in ["location", "budget", "dietary", "smoking", "timeline"]
                if not signals.get(s)
            ],
            completeness    = completeness,
        )
    )
