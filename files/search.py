from models import Candidate, EnrichedIntent, SearchResult

# Hardcoded candidate pool — simulates the vector DB
_CANDIDATES = [
    Candidate("P1", "Priya, 26",
              "Koramangala, vegetarian, non-smoker, room available Feb 1, ₹12,000/mo",
              score=0.0, tags=["koramangala", "vegetarian", "non-smoker", "12000", "feb"]),

    Candidate("P2", "Rohan, 28",
              "Koramangala, non-veg, smoker, room available now, ₹10,000/mo",
              score=0.0, tags=["koramangala", "non-veg", "smoker", "10000"]),

    Candidate("P3", "Sneha, 30",
              "Koramangala, vegetarian, non-smoker, room available March, ₹15,000/mo",
              score=0.0, tags=["koramangala", "vegetarian", "non-smoker", "15000", "march"]),

    Candidate("P4", "Arjun, 24",
              "HSR Layout, vegetarian, non-smoker, ₹11,000/mo",
              score=0.0, tags=["hsr", "vegetarian", "non-smoker", "11000"]),

    Candidate("P5", "Meera, 27",
              "Koramangala, non-veg, non-smoker, room available now, ₹13,000/mo",
              score=0.0, tags=["koramangala", "non-veg", "non-smoker", "13000"]),
]


def search(intent: EnrichedIntent) -> SearchResult:
    """Score each candidate against hard filters. Fully local, no embeddings."""
    hf      = intent.hard_filters
    scored  = []

    for c in _CANDIDATES:
        score = 0.0
        tags  = c.tags

        # Location match
        if hf.location and hf.location.lower().replace(" ", "") in " ".join(tags):
            score += 0.30

        # Dietary
        if hf.dietary:
            if hf.dietary.lower() in tags:
                score += 0.25
            else:
                score -= 0.20   # Hard mismatch penalty

        # Smoking
        if hf.smoking:
            if hf.smoking.lower() in tags:
                score += 0.20
            else:
                score -= 0.15

        # Budget
        if hf.budget_max:
            candidate_budgets = [int(t) for t in tags if t.isdigit() and int(t) > 1000]
            if candidate_budgets:
                cb = candidate_budgets[0]
                if cb <= hf.budget_max:
                    score += 0.20
                elif cb <= hf.budget_max * 1.15:
                    score += 0.05   # slight penalty for slightly over budget

        score = round(max(0.0, min(1.0, score)), 2)
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for score, c in scored:
        c.score = score
        results.append(c)

    top_score = results[0].score if results else 0.0
    scores    = [c.score for c in results]
    spread    = max(scores) - min(scores)
    dist      = "clustered" if spread < 0.25 else "spread"

    return SearchResult(
        candidates         = results,
        top_score          = top_score,
        score_distribution = dist,
    )
