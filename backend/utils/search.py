"""
Semantic search + hard-filter matching over stored intents.

Pipeline:
  1. Domain pre-filter   — block semantically incompatible intent types
  2. Hard-filter check   — domain-specific constraints (dietary, smoking, budget,
                           gender, format, timeline)
  3. Location check      — haversine within source's radius
  4. Vector similarity   — cosine score on embeddings
  5. Score combination   — cosine × location_decay × urgency_boost × age_decay
"""
import re
from datetime import datetime, timezone

import store
from embeddings import embed
from geocode import haversine_km
from vector_search import cosine_top_k

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

INTENT_TYPE_TO_DOMAIN = {
    "flatmate":  1, "housing": 1, "co-living": 1, "room": 1,
    "dating":    2,
    "hiring":    3, "job": 3, "work": 3,
    "buying":    4,
    "selling":   5,
    "activity":  6, "sport": 6,
    "community": 7, "social": 7,
    "other":    15,
}

_COMPATIBLE: dict[int, set[int]] = {
    1:  {1},
    2:  {2},
    3:  {3},
    4:  {4, 5},
    5:  {4, 5},
    6:  {6},
    7:  {7},
    15: set(range(16)),
}

# Which hard-filter keys are meaningful per domain.
# "timeline" is listed here where temporal mismatch can eliminate a match.
_DOMAIN_FILTERS: dict[int, frozenset[str]] = {
    1: frozenset({"dietary", "smoking", "gender_pref", "budget", "timeline"}),  # flatmate
    2: frozenset({"dietary", "smoking", "gender_pref"}),                        # dating
    3: frozenset({"budget", "skill_level", "format", "timeline"}),              # hiring
    4: frozenset({"budget", "item_type"}),                                      # buying
    5: frozenset({"budget", "item_type"}),                                      # selling
    6: frozenset({"skill_level", "format", "gender_pref", "timeline"}),         # activity
    7: frozenset({"format"}),                                                   # community
    15: frozenset({"dietary", "smoking", "budget", "gender_pref"}),             # unclassified
}


def _domain_ok(src: dict, cand: dict) -> bool:
    src_d  = src.get("domain") or 15
    cand_d = cand.get("domain") or 15
    if src_d == 15:
        return True
    if cand_d == 15:
        return False
    return cand_d in _COMPATIBLE.get(src_d, set())


# ---------------------------------------------------------------------------
# Timeline parsing
# ---------------------------------------------------------------------------

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_NOW_WORDS   = {"immediately", "asap", "urgent", "right now", "today", "tonight"}
_NEVER_WORDS = {"flexible", "anytime", "whenever", "no deadline", "open"}


def parse_timeline(s: str) -> tuple[float, float] | None:
    """
    Parse a router-provided timeline string into (time_start, time_end) unix
    timestamps. Returns None if the string is too vague to machine-compare.

    Handles:
      - ISO dates:         "before 2025-01-31"  →  (0, ts)
      - Month names:       "May" / "end of June"  →  (month_start, month_end)
      - Relative urgency:  "immediately", "ASAP"  →  (now, now + 48h)
      - "flexible"/"open"  →  None  (no constraint)
      - "within N weeks/days" → (now, now + N*period)
    """
    if not s:
        return None
    sl = s.strip().lower()

    if any(w in sl for w in _NEVER_WORDS):
        return None

    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()

    # Urgency words → tight window
    if any(w in sl for w in _NOW_WORDS):
        return (now_ts, now_ts + 48 * 3600)

    # "within N days/weeks"
    m = re.search(r"within\s+(\d+)\s*(day|week|month)", sl)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n if unit == "day" else n * 7 if unit == "week" else n * 30
        return (now_ts, now_ts + days * 86400)

    # ISO date with optional qualifier
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          tzinfo=timezone.utc)
            ts = dt.timestamp()
            if "before" in sl or "by" in sl or "end of" in sl:
                return (0.0, ts)
            if "after" in sl or "from" in sl or "starting" in sl:
                return (ts, 0.0)
            # Bare date → treat as target day
            return (ts, ts + 86400)
        except ValueError:
            pass

    # Month name
    for month_name, month_num in _MONTHS.items():
        if month_name in sl:
            year = now.year if month_num >= now.month else now.year + 1
            try:
                start = datetime(year, month_num, 1, tzinfo=timezone.utc)
                # end = first day of next month
                end_m = month_num % 12 + 1
                end_y = year if end_m > 1 else year + 1
                end   = datetime(end_y, end_m, 1, tzinfo=timezone.utc)
                return (start.timestamp(), end.timestamp())
            except ValueError:
                pass

    return None  # couldn't parse — treat as no constraint


def timelines_overlap(src_tl: str | None, cand_tl: str | None) -> bool:
    """
    Return False only when both timelines parse AND are provably non-overlapping.
    Unparseable or missing timelines → permissive (True).
    """
    if not src_tl or not cand_tl:
        return True

    sp = parse_timeline(src_tl)
    cp = parse_timeline(cand_tl)

    if sp is None or cp is None:
        return True  # can't determine → don't block

    s_start, s_end = sp
    c_start, c_end = cp

    # Both are ranges → check overlap
    # 0 means "open" on that side
    if s_end and c_start and s_end < c_start:
        return False  # source ends before candidate starts
    if c_end and s_start and c_end < s_start:
        return False  # candidate ends before source starts

    return True


# ---------------------------------------------------------------------------
# Hard-filter compatibility (domain-aware)
# ---------------------------------------------------------------------------

def _filters_ok(src: dict, cand: dict) -> bool:
    domain = src.get("domain") or 15
    active = _DOMAIN_FILTERS.get(domain, _DOMAIN_FILTERS[15])

    if "dietary" in active:
        s_diet = (src.get("dietary") or "").lower()
        c_diet = (cand.get("dietary") or "").lower()
        if "vegetarian" in s_diet and c_diet and "non" in c_diet and "veg" in c_diet:
            return False
        if "vegetarian" in c_diet and s_diet and "non" in s_diet and "veg" in s_diet:
            return False

    if "smoking" in active:
        s_ns = "non-smoker" in (src.get("smoking") or "").lower()
        c_ns = "non-smoker" in (cand.get("smoking") or "").lower()
        s_sm = "smoker" in (src.get("smoking") or "").lower() and not s_ns
        c_sm = "smoker" in (cand.get("smoking") or "").lower() and not c_ns
        if s_ns and c_sm:
            return False
        if c_ns and s_sm:
            return False

    if "budget" in active:
        s_min = src.get("budget_min") or 0
        s_max = src.get("budget_max") or 0
        c_min = cand.get("budget_min") or 0
        c_max = cand.get("budget_max") or 0
        if s_max and c_min and s_max < c_min:
            return False
        if c_max and s_min and c_max < s_min:
            return False

    if "gender_pref" in active:
        s_gp = (src.get("gender_pref") or "").lower()
        c_gp = (cand.get("gender_pref") or "").lower()
        if s_gp and c_gp:
            if "female" in s_gp and "male" in c_gp and "female" not in c_gp:
                return False
            if "male" in s_gp and "female" not in s_gp and "female" in c_gp:
                return False

    if "format" in active:
        s_fmt = (src.get("format") or "").lower()
        c_fmt = (cand.get("format") or "").lower()
        if s_fmt and c_fmt:
            s_online = "online" in s_fmt or "remote" in s_fmt
            c_online = "online" in c_fmt or "remote" in c_fmt
            s_person = "in-person" in s_fmt or "offline" in s_fmt
            c_person = "in-person" in c_fmt or "offline" in c_fmt
            if s_online and c_person:
                return False
            if s_person and c_online:
                return False

    if "timeline" in active:
        if not timelines_overlap(src.get("timeline"), cand.get("timeline")):
            return False

    return True


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

def _location_check(src: dict, cand: dict) -> tuple[bool, float | None]:
    if bool(cand.get("flags", 0) & 2):   # remote-ok flag
        return True, None

    s_lat, s_lng = src.get("lat"), src.get("lng")
    c_lat, c_lng = cand.get("lat"), cand.get("lng")
    if s_lat is None or s_lng is None or c_lat is None or c_lng is None:
        return True, None

    dist = haversine_km(s_lat, s_lng, c_lat, c_lng)
    radius = src.get("radius") or 10.0
    return dist <= radius, round(dist, 2)


# ---------------------------------------------------------------------------
# Score combination
# ---------------------------------------------------------------------------

def _age_decay(created_at: str | None) -> float:
    """
    Reduce score for stale candidates.
      < 7 days   → 1.00  (fresh)
      7–30 days  → 0.90
      30–90 days → 0.80
      > 90 days  → 0.65  (likely stale)
    """
    if not created_at:
        return 1.0
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 1.0

    if age_days < 7:
        return 1.00
    if age_days < 30:
        return 0.90
    if age_days < 90:
        return 0.80
    return 0.65


def _combined_score(
    cosine: float,
    dist_km: float | None,
    src_radius: float,
    is_urgent: bool,
    cand_created_at: str | None,
) -> float:
    """
    Final score = cosine × location_decay × age_decay × urgency_boost

    location_decay: 1.0 at dist=0, 0.7 at radius edge (max 30% penalty)
    age_decay:      1.0 for fresh intents, down to 0.65 for >90-day-old ones
    urgency_boost:  +5% when the source intent is urgent (needs fast match)
    """
    score = cosine

    # Location decay
    if dist_km is not None:
        score *= max(0.7, 1.0 - 0.3 * dist_km / max(src_radius, 1.0))

    # Age decay on candidate
    score *= _age_decay(cand_created_at)

    # Urgency boost: urgent source deserves fresher, closer matches first
    if is_urgent:
        score *= 1.05

    return score


# ---------------------------------------------------------------------------
# Public search function
# ---------------------------------------------------------------------------

def search_by_vector(
    query_vec,
    source_extracted: dict,
    exclude_agent_id: str,
    top_n: int = 5,
    threshold: float = 0.65,
) -> list[dict]:
    src = source_extracted
    src_radius  = src.get("radius") or 10.0
    is_urgent   = bool(src.get("urgency")) or bool(src.get("flags", 0) & 1)

    candidates = [
        i for i in store.all_intents()
        if i.get("status") == "active"
        and i.get("agent_id") != exclude_agent_id
        and i.get("embedding")
    ]

    fetch_k   = max(top_n * 10, 50)
    top_cosine = cosine_top_k(query_vec, candidates, k=fetch_k)

    results = []
    for doc, cosine in top_cosine:
        if cosine < threshold:
            break

        cand_ext = doc.get("extracted") or {}

        if not _domain_ok(src, cand_ext):
            continue
        if not _filters_ok(src, cand_ext):
            continue

        within, dist_km = _location_check(src, cand_ext)
        if not within:
            continue

        score = _combined_score(
            cosine, dist_km, src_radius,
            is_urgent, doc.get("created_at"),
        )

        results.append({
            "intent_id":   doc["intent_id"],
            "agent_id":    doc["agent_id"],
            "text":        doc["text"],
            "relevance":   round(score, 4),
            "cosine":      round(cosine, 4),
            "distance_km": dist_km,
            "intent_type": cand_ext.get("intent_type", ""),
            "location":    cand_ext.get("location_query", ""),
            "tags":        cand_ext.get("tags") or [],
        })

    results.sort(key=lambda r: r["relevance"], reverse=True)
    return results[:top_n]


async def search_by_text(
    query_text: str,
    source_extracted: dict,
    exclude_agent_id: str,
    top_n: int = 5,
    threshold: float = 0.65,
) -> list[dict]:
    vec = await embed(query_text)
    return search_by_vector(vec, source_extracted, exclude_agent_id, top_n, threshold)
