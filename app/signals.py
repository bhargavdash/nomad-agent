"""SignalExtractor — pure-Python derivation of travel signals from trip params.

No LLM call. Deterministic. Runs once at the start of every pipeline.
The output (`TravelSignals`) is consumed by every downstream agent to
shape search queries and weighting.

Matches section 3 of AI_INTEGRATION_PLAN.md.

Why this layer exists:
  Without it, every Goa trip gets the same query regardless of dates.
  With it, Goa-in-December (peak season, NYE festival, "very_high" crowd)
  produces fundamentally different search queries than Goa-in-July
  (monsoon, "low" crowd, indoor-activities focus).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.schemas import TripParams

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output shape (kept backward-compatible — downstream agents read these fields)
# ---------------------------------------------------------------------------


@dataclass
class TravelSignals:
    # Geography
    region: str  # "india" | "southeast_asia" | "europe" | "north_america" | "oceania" | "unknown"

    # Time / weather
    season: str  # "winter" | "spring" | "summer" | "monsoon" | "autumn" | "peak" | "shoulder" | "unknown"
    weather_hint: str | None  # short tag, e.g. "monsoon-flooding-risk", "snow-pass-closures"

    # Festivals
    is_festival_window: bool
    festival_name: str | None  # first active festival (back-compat)
    active_festivals: list[str]  # all festivals overlapping the trip window

    # Crowds
    crowd_level: str  # "low" | "moderate" | "peak" | "very_peak"

    # Trip shape (from trip_params, surfaced for downstream convenience)
    budget_tier: str  # "shoestring" | "mid" | "premium" | "luxury"
    pace_density: int  # stops per day: 3 | 4 | 5

    # Search shaping
    vibe_source_weights: dict = field(default_factory=dict)
    query_modifiers: list[str] = field(default_factory=list)

    # User-visible warnings (rendered in UI, e.g. "Monsoon — many beaches closed")
    warnings: list[str] = field(default_factory=list)

    # Soft, deterministic practical tips for the trip's season (not risks —
    # "pack layers, Dec nights are cold", "peak season, book ahead"). The
    # synthesizer weaves these into the itinerary the way a good human planner
    # would. Distinct from `warnings`, which are hazards surfaced on Day 1.
    seasonal_tips: list[str] = field(default_factory=list)

    # Local currency for budget figures, e.g. "INR (₹)". None when ambiguous —
    # the synthesizer is then told to infer it from the destination.
    currency_hint: str | None = None

    # Canonical must-visit landmarks seeded by LLM, independent of vibe-extraction.
    top_anchors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lookup tables — region detection
# ---------------------------------------------------------------------------

_REGION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "india": (
        "india", "goa", "manali", "jaipur", "delhi", "mumbai", "bangalore",
        "kerala", "ladakh", "kashmir", "rajasthan", "varanasi", "udaipur",
        "chennai", "pushkar", "rishikesh", "agra", "darjeeling", "shimla",
    ),
    "southeast_asia": (
        "bali", "indonesia", "thailand", "bangkok", "phuket", "vietnam",
        "hanoi", "ho chi minh", "singapore", "malaysia", "kuala lumpur",
        "philippines", "manila", "cambodia", "angkor", "laos", "myanmar",
        "sri lanka",
    ),
    "europe": (
        "paris", "france", "italy", "rome", "milan", "venice", "florence",
        "spain", "madrid", "barcelona", "germany", "berlin", "munich",
        "london", "uk", "england", "scotland", "amsterdam", "netherlands",
        "greece", "athens", "santorini", "portugal", "lisbon", "switzerland",
        "vienna", "prague", "budapest",
    ),
    "north_america": (
        "new york", "nyc", "usa", "united states", "los angeles", "san francisco",
        "chicago", "miami", "las vegas", "seattle", "boston",
        "canada", "toronto", "vancouver", "montreal",
        "mexico", "mexico city", "cancun",
    ),
    "oceania": (
        "sydney", "australia", "melbourne", "new zealand", "auckland", "queenstown",
    ),
}

# Indian hill stations — peak in summer (Apr–Jun), counter to plains-India peak (Oct–Feb).
_HILL_STATION_KEYWORDS = ("manali", "shimla", "ladakh", "darjeeling", "kashmir", "mussoorie")

# Destinations whose "peak" is the dry winter (Nov–Feb).
_WINTER_PEAK_KEYWORDS = ("goa", "kerala", "rajasthan", "jaipur", "udaipur", "pushkar", "agra")


# ---------------------------------------------------------------------------
# Festival database
# Stored as (start_month, start_day, end_month, end_day, name, crowd_impact).
# Year-wrap handled by checking month tuples.
# Substring-keyed by destination — small set, MVP scope.
# ---------------------------------------------------------------------------

_Festival = tuple[int, int, int, int, str, str]  # high | medium | low

_FESTIVALS: dict[str, list[_Festival]] = {
    "goa": [
        (12, 20, 1, 5, "Christmas/NYE", "high"),
        (12, 27, 12, 31, "Sunburn Festival", "high"),
        (2, 10, 2, 20, "Goa Carnival", "high"),
        (3, 10, 3, 20, "Holi", "medium"),
    ],
    "manali": [
        (10, 1, 10, 15, "Dussehra", "high"),
    ],
    "jaipur": [
        (1, 19, 1, 23, "Jaipur Literature Festival", "medium"),
        (10, 28, 11, 5, "Diwali", "high"),
        (3, 10, 3, 20, "Holi", "high"),
    ],
    "delhi": [
        (10, 28, 11, 5, "Diwali", "high"),
        (3, 10, 3, 20, "Holi", "high"),
    ],
    "udaipur": [
        (10, 28, 11, 5, "Diwali", "medium"),
    ],
    "varanasi": [
        (10, 28, 11, 5, "Diwali", "high"),
        (11, 1, 11, 30, "Dev Deepawali", "high"),
    ],
    "pushkar": [
        (11, 1, 11, 15, "Pushkar Camel Fair", "high"),
    ],
    "kerala": [
        (8, 25, 9, 10, "Onam", "medium"),
    ],
    "paris": [
        (7, 12, 7, 16, "Bastille Day", "high"),
        (11, 25, 12, 31, "Christmas Markets", "medium"),
    ],
    "munich": [
        (9, 16, 10, 3, "Oktoberfest", "high"),
    ],
    "bali": [
        (3, 10, 3, 15, "Nyepi (Day of Silence)", "medium"),
    ],
    "thailand": [
        (4, 13, 4, 15, "Songkran", "high"),
    ],
    "bangkok": [
        (4, 13, 4, 15, "Songkran", "high"),
    ],
    "new york": [
        (12, 15, 1, 2, "Christmas/NYE", "high"),
        (11, 22, 11, 28, "Thanksgiving", "high"),
        (7, 2, 7, 5, "July 4th", "medium"),
    ],
    "nyc": [
        (12, 15, 1, 2, "Christmas/NYE", "high"),
        (11, 22, 11, 28, "Thanksgiving", "high"),
        (7, 2, 7, 5, "July 4th", "medium"),
    ],
    "rio": [
        (2, 10, 2, 20, "Carnival", "high"),
    ],
    "japan": [
        (3, 25, 4, 10, "Cherry Blossom (Hanami)", "high"),
    ],
    "tokyo": [
        (3, 25, 4, 10, "Cherry Blossom (Hanami)", "high"),
    ],
}


# ---------------------------------------------------------------------------
# Vibe → source weights
# Returns weights for ("youtube", "reddit", "blog"). Sum is normalised to 1.0
# so downstream synthesizer can use them as proportions directly.
# ---------------------------------------------------------------------------

# Per-vibe RAW weights (not normalised). Aggregation = average across vibes.
_VIBE_WEIGHTS_RAW: dict[str, dict[str, float]] = {
    # adventure-leaning vibes — Shorts capture authentic raw content
    "adventure":          {"youtube": 1.5, "reddit": 1.2, "blog": 0.7},
    "off the beaten path": {"youtube": 1.4, "reddit": 1.5, "blog": 0.5},
    "hidden gems":        {"youtube": 1.4, "reddit": 1.5, "blog": 0.5},
    "nightlife":          {"youtube": 1.5, "reddit": 1.3, "blog": 0.6},
    "nature":             {"youtube": 1.3, "reddit": 1.0, "blog": 1.0},
    "beaches":            {"youtube": 1.3, "reddit": 1.0, "blog": 1.0},
    "beach":              {"youtube": 1.3, "reddit": 1.0, "blog": 1.0},

    # blog-leaning vibes — curated, written guides best
    "luxury":             {"youtube": 0.6, "reddit": 0.7, "blog": 1.5},
    "iconic":             {"youtube": 0.7, "reddit": 0.7, "blog": 1.4},
    "first time":         {"youtube": 0.8, "reddit": 0.9, "blog": 1.3},
    "culture":            {"youtube": 0.8, "reddit": 1.0, "blog": 1.3},
    "history":            {"youtube": 0.8, "reddit": 0.9, "blog": 1.3},
    "spiritual":          {"youtube": 0.9, "reddit": 1.0, "blog": 1.3},
    "relaxation":         {"youtube": 1.0, "reddit": 0.8, "blog": 1.2},

    # reddit-leaning vibes — communities best for tips and warnings
    "budget":             {"youtube": 1.0, "reddit": 1.5, "blog": 0.8},
    "backpacking":        {"youtube": 1.1, "reddit": 1.5, "blog": 0.7},
    "solo":               {"youtube": 1.0, "reddit": 1.5, "blog": 0.8},

    # balanced vibes
    "foodie":             {"youtube": 1.3, "reddit": 1.3, "blog": 1.0},
    "street food":        {"youtube": 1.3, "reddit": 1.4, "blog": 0.9},
    "shopping":           {"youtube": 1.0, "reddit": 1.0, "blog": 1.2},
    "family":             {"youtube": 1.0, "reddit": 1.1, "blog": 1.2},
    "romantic":           {"youtube": 0.9, "reddit": 0.8, "blog": 1.3},
}


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    """Normalise weights so they sum to 1.0. Defensive against zero sums."""
    total = sum(weights.values())
    if total <= 0:
        # Fallback to even split.
        return {"youtube": 1 / 3, "reddit": 1 / 3, "blog": 1 / 3}
    return {k: round(v / total, 4) for k, v in weights.items()}


def _vibe_weights(vibes: list[str]) -> dict[str, float]:
    """Average per-vibe raw weights, then normalise to sum=1.0.

    Unknown vibes are ignored. Empty list → even split.
    """
    matched: list[dict[str, float]] = []
    for v in vibes:
        key = v.strip().lower()
        if key in _VIBE_WEIGHTS_RAW:
            matched.append(_VIBE_WEIGHTS_RAW[key])

    if not matched:
        return {"youtube": 1 / 3, "reddit": 1 / 3, "blog": 1 / 3}

    avg = {
        "youtube": sum(w["youtube"] for w in matched) / len(matched),
        "reddit": sum(w["reddit"] for w in matched) / len(matched),
        "blog": sum(w["blog"] for w in matched) / len(matched),
    }
    return _normalise(avg)


# ---------------------------------------------------------------------------
# Region + season detection
# ---------------------------------------------------------------------------


def _detect_region(destination_lower: str) -> str:
    for region, keywords in _REGION_KEYWORDS.items():
        if any(kw in destination_lower for kw in keywords):
            return region
    return "unknown"


def _midpoint_month(date_from: str | None, date_to: str | None) -> int | None:
    """Return the month of the trip's midpoint date. None if dates unparseable."""
    if not date_from:
        return None
    try:
        d1 = date.fromisoformat(date_from)
    except ValueError:
        return None
    if date_to:
        try:
            d2 = date.fromisoformat(date_to)
            mid = d1 + (d2 - d1) / 2
            return mid.month
        except ValueError:
            pass
    return d1.month


def _infer_season(
    month: int | None, region: str, destination_lower: str
) -> tuple[str, str | None, list[str]]:
    """Return (season, weather_hint, query_modifiers).

    Per-region rules. Hill-station override for India.
    """
    if month is None:
        return "unknown", None, []

    # Hill stations: Apr-Jun is peak (escape from plains heat).
    if any(k in destination_lower for k in _HILL_STATION_KEYWORDS):
        if month in (4, 5, 6):
            return "peak", None, ["summer", "hill station season"]
        if month in (7, 8, 9):
            hint = None
            if "manali" in destination_lower or "kashmir" in destination_lower:
                hint = "monsoon-landslide-risk"
            return "monsoon", hint, ["monsoon", "indoor activities"]
        if month in (11, 12, 1, 2):
            hint = None
            if "ladakh" in destination_lower or "manali" in destination_lower:
                hint = "snow-pass-closures"
            return "winter", hint, ["winter", "snow"]
        return "shoulder", None, ["shoulder season"]

    # India (plains): Jun-Sep monsoon, Oct-Feb peak/winter, Mar-May summer-shoulder.
    if region == "india":
        if month in (6, 7, 8, 9):
            return "monsoon", "monsoon-flooding-risk", ["monsoon", "indoor activities", "waterfalls"]
        if any(k in destination_lower for k in _WINTER_PEAK_KEYWORDS) and month in (11, 12, 1, 2):
            return "peak", None, ["peak season", "best weather"]
        if month in (10, 11, 12, 1, 2):
            return "winter", None, ["winter", "cool weather"]
        # Mar-May: hot summer in plains.
        return "summer", "heatwave-risk", ["summer", "early morning starts"]

    # Southeast Asia: May-Oct rainy, Nov-Apr dry/peak.
    if region == "southeast_asia":
        if month in (5, 6, 7, 8, 9, 10):
            return "monsoon", "monsoon-flooding-risk", ["rainy season", "indoor activities"]
        return "peak", None, ["dry season", "peak season"]

    # Europe: Jun-Aug peak, Mar-May & Sep-Oct shoulder, Nov-Feb winter.
    if region == "europe":
        if month in (6, 7, 8):
            return "peak", None, ["summer", "peak season"]
        if month in (3, 4, 5, 9, 10):
            return "shoulder", None, ["shoulder season", "moderate weather"]
        return "winter", None, ["winter", "cozy"]

    # North America: same as Europe for our MVP.
    if region == "north_america":
        if month in (6, 7, 8):
            return "peak", None, ["summer", "peak season"]
        if month in (3, 4, 5, 9, 10):
            return "shoulder", None, ["shoulder season"]
        return "winter", None, ["winter"]

    # Oceania: INVERTED — Dec-Feb peak (summer), Jun-Aug winter.
    if region == "oceania":
        if month in (12, 1, 2):
            return "peak", None, ["summer", "peak season"]
        if month in (6, 7, 8):
            return "winter", None, ["winter"]
        return "shoulder", None, ["shoulder season"]

    # Unknown region — Northern Hemisphere generic.
    if month in (12, 1, 2):
        return "winter", None, ["winter"]
    if month in (3, 4, 5):
        return "spring", None, ["spring"]
    if month in (6, 7, 8):
        return "summer", None, ["summer"]
    return "autumn", None, ["autumn"]


# ---------------------------------------------------------------------------
# Festival overlap detection
# ---------------------------------------------------------------------------


def _date_in_window(
    check: date, start_month: int, start_day: int, end_month: int, end_day: int
) -> bool:
    """Check if `check` falls in the window. Handles year-wrap (e.g. Dec 20 → Jan 5)."""
    year = check.year
    start = date(year, start_month, start_day)
    if (end_month, end_day) >= (start_month, start_day):
        end = date(year, end_month, end_day)
    else:
        # Year-wrap: end is in next calendar year relative to start
        end = date(year + 1, end_month, end_day)
        if check < start:
            # Maybe we're in the tail end of last year's window
            start_prev = date(year - 1, start_month, start_day)
            end_prev = date(year, end_month, end_day)
            return start_prev <= check <= end_prev
    return start <= check <= end


def _windows_overlap(
    trip_start: date,
    trip_end: date,
    fest_start_m: int,
    fest_start_d: int,
    fest_end_m: int,
    fest_end_d: int,
) -> bool:
    """Does any day of the trip fall inside the festival window?"""
    # Sample both endpoints + a few midpoints to catch overlap cheaply.
    delta = (trip_end - trip_start).days
    samples = [trip_start + (trip_end - trip_start) * (i / max(delta, 1)) for i in range(delta + 1)]
    for d in samples:
        if isinstance(d, date) and _date_in_window(
            d, fest_start_m, fest_start_d, fest_end_m, fest_end_d
        ):
            return True
    return False


def _find_active_festivals(
    destination_lower: str, date_from: str | None, date_to: str | None
) -> list[tuple[str, str]]:
    """Return [(festival_name, crowd_impact), ...] for festivals overlapping the trip."""
    if not date_from:
        return []
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to) if date_to else d_from
    except ValueError:
        return []

    matches: list[tuple[str, str]] = []
    for key, festivals in _FESTIVALS.items():
        if key not in destination_lower:
            continue
        for sm, sd, em, ed, name, impact in festivals:
            if _windows_overlap(d_from, d_to, sm, sd, em, ed):
                matches.append((name, impact))
    # Deduplicate (same festival name from overlapping keys).
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for name, impact in matches:
        if name not in seen:
            seen.add(name)
            unique.append((name, impact))
    return unique


# ---------------------------------------------------------------------------
# Crowd level + warnings + query modifiers
# ---------------------------------------------------------------------------


def _crowd_level(season: str, festivals: list[tuple[str, str]]) -> str:
    has_high = any(impact == "high" for _, impact in festivals)
    if season == "peak" and has_high:
        return "very_peak"
    if season == "peak":
        return "peak"
    if has_high:
        return "peak"
    if season == "shoulder":
        return "moderate"
    if season in ("monsoon", "winter"):
        return "low"
    return "moderate"


def _build_warnings(season: str, region: str, destination_lower: str, weather_hint: str | None) -> list[str]:
    warnings: list[str] = []
    if season == "monsoon" and region in ("india", "southeast_asia"):
        warnings.append(
            "Monsoon season — expect heavy rain, some attractions may be closed, "
            "road conditions can be poor."
        )
    if weather_hint == "snow-pass-closures":
        warnings.append("Heavy snow possible — high passes (e.g. Rohtang) may be closed.")
    if weather_hint == "heatwave-risk":
        warnings.append("Hot summer in the plains — plan outdoor activity for early morning or evening.")
    if weather_hint == "monsoon-landslide-risk":
        warnings.append("Monsoon in hill region — landslides and road closures possible.")
    return warnings


def _build_seasonal_tips(
    season: str, region: str, month: int | None, destination_lower: str
) -> list[str]:
    """Soft, deterministic practical tips for the trip's season (not hazards).

    These mirror what a good human planner volunteers — "book ahead in peak
    season", "Dec nights are cold, pack layers" — and give the synthesizer
    concrete practical content to weave in (the GPT-5.5 benchmark surfaced
    exactly these). Pure function of season/region/month; no I/O.
    """
    tips: list[str] = []
    cold_now = month in (11, 12, 1, 2)
    is_hill = any(k in destination_lower for k in _HILL_STATION_KEYWORDS)

    if season in ("peak", "very_peak"):
        tips.append(
            "Peak season — book trains, hotels, and marquee experiences well ahead."
        )
    if cold_now and (region == "india" or is_hill):
        tips.append(
            "Nights get cold this time of year — pack layers/thermals, "
            "especially in desert or hill areas."
        )
    if season == "monsoon":
        tips.append(
            "Monsoon — carry rain gear; expect occasional road or attraction "
            "closures on heavy-rain days."
        )
    if season == "summer" and region == "india":
        tips.append(
            "Midday heat is intense — front-load sightseeing to early morning "
            "and rest through midday."
        )
    return tips


# Destination-keyword → local currency. Multi-currency regions (Europe, SEA,
# Americas, Oceania) need country-level overrides; India is unambiguous by
# region. Anything not matched returns None and the synthesizer infers it.
_CURRENCY_OVERRIDES: dict[str, str] = {
    "thailand": "THB (฿)", "bangkok": "THB (฿)", "phuket": "THB (฿)",
    "japan": "JPY (¥)", "tokyo": "JPY (¥)",
    "vietnam": "VND (₫)", "hanoi": "VND (₫)", "ho chi minh": "VND (₫)",
    "singapore": "SGD (S$)",
    "indonesia": "IDR (Rp)", "bali": "IDR (Rp)",
    "malaysia": "MYR (RM)", "kuala lumpur": "MYR (RM)",
    "philippines": "PHP (₱)", "manila": "PHP (₱)",
    "sri lanka": "LKR (Rs)",
    "uk": "GBP (£)", "london": "GBP (£)", "england": "GBP (£)", "scotland": "GBP (£)",
    "switzerland": "CHF", "zurich": "CHF", "geneva": "CHF",
    "usa": "USD ($)", "united states": "USD ($)", "new york": "USD ($)",
    "nyc": "USD ($)", "los angeles": "USD ($)", "san francisco": "USD ($)",
    "canada": "CAD (C$)", "toronto": "CAD (C$)", "vancouver": "CAD (C$)",
    "mexico": "MXN ($)", "cancun": "MXN ($)",
    "australia": "AUD (A$)", "sydney": "AUD (A$)", "melbourne": "AUD (A$)",
    "new zealand": "NZD (NZ$)", "auckland": "NZD (NZ$)", "queenstown": "NZD (NZ$)",
}


def _currency_hint(region: str, destination_lower: str) -> str | None:
    """Local currency for budget figures. India is unambiguous by region;
    multi-currency regions use country overrides; Eurozone defaults to EUR.
    Returns None when unknown so the synthesizer infers from the destination."""
    if region == "india":
        return "INR (₹)"
    for keyword, currency in _CURRENCY_OVERRIDES.items():
        if keyword in destination_lower:
            return currency
    if region == "europe":
        # UK / Switzerland already handled above; default the rest to euro.
        return "EUR (€)"
    return None


def _build_query_modifiers(
    base: list[str],
    season: str,
    crowd_level: str,
    festivals: list[tuple[str, str]],
    vibes: list[str],
) -> list[str]:
    mods = list(base)  # season-derived modifiers passed in

    if crowd_level == "very_peak":
        mods.extend(["avoid crowds", "hidden gems", "off-the-beaten-path", "early morning"])
    elif crowd_level == "peak":
        mods.extend(["less crowded", "local favorites"])
    elif crowd_level == "low" and season != "monsoon":
        mods.append("off-season")

    for name, _ in festivals:
        mods.append(f"{name} celebrations")

    vibes_lower = {v.lower() for v in vibes}
    if "budget" in vibes_lower or "backpacking" in vibes_lower:
        mods.append("cheap eats")
    if "luxury" in vibes_lower:
        mods.append("best reviewed")
    if "foodie" in vibes_lower or "street food" in vibes_lower:
        mods.append("local cuisine")
    if "culture" in vibes_lower or "history" in vibes_lower:
        mods.append("cultural landmarks")

    # Mix in raw vibe terms (they're useful search words).
    for v in vibes:
        if v and v.lower() not in {q.lower() for q in mods}:
            mods.append(v)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for m in mods:
        if m.lower() not in seen:
            seen.add(m.lower())
            out.append(m)
    return out


# ---------------------------------------------------------------------------
# Trip-shape lookups
# ---------------------------------------------------------------------------

_BUDGET_TIER = {
    "Low": "shoestring",
    "Medium": "mid",
    "High": "premium",
    "Very-High": "luxury",
}

_PACE_DENSITY = {
    "Slow & Soulful": 3,
    "Balanced": 4,
    "Action-Packed": 5,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_signals(trip_params: TripParams) -> TravelSignals:
    """Derive deterministic signals from trip parameters. No LLM."""
    destination_lower = trip_params.destination.lower()
    region = _detect_region(destination_lower)
    month = _midpoint_month(trip_params.date_from, trip_params.date_to)

    season, weather_hint, season_modifiers = _infer_season(month, region, destination_lower)

    festivals = _find_active_festivals(
        destination_lower, trip_params.date_from, trip_params.date_to
    )
    is_festival_window = len(festivals) > 0
    festival_name = festivals[0][0] if festivals else None
    active_festival_names = [name for name, _ in festivals]

    crowd_level = _crowd_level(season, festivals)
    budget_tier = _BUDGET_TIER.get(trip_params.budget, "mid")
    pace_density = _PACE_DENSITY.get(trip_params.pace, 4)
    vibe_source_weights = _vibe_weights(trip_params.vibes)
    query_modifiers = _build_query_modifiers(
        season_modifiers, season, crowd_level, festivals, trip_params.vibes
    )
    warnings = _build_warnings(season, region, destination_lower, weather_hint)
    seasonal_tips = _build_seasonal_tips(season, region, month, destination_lower)
    currency_hint = _currency_hint(region, destination_lower)

    return TravelSignals(
        region=region,
        season=season,
        weather_hint=weather_hint,
        is_festival_window=is_festival_window,
        festival_name=festival_name,
        active_festivals=active_festival_names,
        crowd_level=crowd_level,
        budget_tier=budget_tier,
        pace_density=pace_density,
        vibe_source_weights=vibe_source_weights,
        query_modifiers=query_modifiers,
        warnings=warnings,
        seasonal_tips=seasonal_tips,
        currency_hint=currency_hint,
    )


# ---------------------------------------------------------------------------
# LLM fallback for unknown destinations
#
# The keyword-based _REGION_KEYWORDS map covers ~76 popular destinations but
# users can pick any destination on Earth. When the map misses, region falls
# back to "unknown" which causes season inference to degrade to Northern-
# Hemisphere defaults — wrong for half the globe. This enrichment fires only
# on the unknown path, makes one cheap LLM call, and caches the result per
# destination so repeated runs don't re-pay the cost.
# ---------------------------------------------------------------------------

_LLM_REGION_CACHE: dict[str, "_DestinationClassification"] = {}
_LLM_ANCHOR_CACHE: dict[str, list[str]] = {}

# Anchors that are geographically NOT inside a destination but the LLM commonly
# hallucinates because they're nearby or frequently co-mentioned. Keys are
# destination substrings (lowercased); values are anchor substrings to drop.
_ANCHOR_GEO_EXCLUSIONS: dict[str, list[str]] = {
    "rajasthan": ["taj mahal", "agra fort", "fatehpur sikri", "agra"],
    "goa": ["hampi", "mumbai", "pune", "mysore"],
    "kerala": ["hampi", "mysore", "goa", "coorg"],
    "manali": ["shimla", "chandigarh", "amritsar"],
    "jaipur": ["taj mahal", "agra"],
}

# Regions the keyword map already understands. The LLM is asked to map to one
# of these when possible so the existing _infer_season rules apply directly.
_KNOWN_REGIONS = frozenset(
    {"india", "southeast_asia", "europe", "north_america", "oceania"}
)


@dataclass(frozen=True)
class _DestinationClassification:
    region: str
    hemisphere: Literal["north", "south"]


def _infer_season_with_hemisphere(
    month: int | None,
    region: str,
    destination_lower: str,
    hemisphere: Literal["north", "south"],
) -> tuple[str, str | None, list[str]]:
    """Like `_infer_season` but uses hemisphere for unknown-region destinations.

    Known regions (india/southeast_asia/europe/north_america/oceania) keep
    their specific rules. For everything else (south_america, africa, etc.),
    fall back to generic 4-quarter buckets, inverted in the south.
    """
    if region in _KNOWN_REGIONS:
        return _infer_season(month, region, destination_lower)
    if month is None:
        return "unknown", None, []
    if hemisphere == "south":
        if month in (6, 7, 8):
            return "winter", None, ["winter"]
        if month in (9, 10, 11):
            return "spring", None, ["spring"]
        if month in (12, 1, 2):
            return "summer", None, ["summer"]
        return "autumn", None, ["autumn"]
    # North (default)
    if month in (12, 1, 2):
        return "winter", None, ["winter"]
    if month in (3, 4, 5):
        return "spring", None, ["spring"]
    if month in (6, 7, 8):
        return "summer", None, ["summer"]
    return "autumn", None, ["autumn"]


async def _classify_destination_via_llm(
    destination: str,
) -> _DestinationClassification | None:
    """One small LLM call returning {region, hemisphere}. None on failure.

    Restricts region to the known set when possible; the LLM may also return
    "south_america", "africa", "middle_east" for destinations that don't fit
    the existing buckets — hemisphere then drives season fallback.
    """
    from pydantic import BaseModel, Field

    from app.llm.factory import get_llm

    class _Classification(BaseModel):
        region: Literal[
            "india",
            "southeast_asia",
            "europe",
            "north_america",
            "oceania",
            "south_america",
            "africa",
            "middle_east",
        ] = Field(..., description="Macro-region this destination belongs to.")
        hemisphere: Literal["north", "south"] = Field(
            ..., description="Climate hemisphere — drives seasonal inference."
        )

    system = (
        "You classify travel destinations by region and hemisphere so a "
        "travel-planning system can pick the right seasonal rules. "
        "Output JSON only with keys exactly 'region' and 'hemisphere'."
    )
    user = (
        f"Destination: {destination}\n\n"
        "Pick the region (key 'region') from one of: india, southeast_asia, "
        "europe, north_america, oceania, south_america, africa, middle_east. "
        "Use the closest fit. Hemisphere (key 'hemisphere') is 'north' or 'south'."
    )
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_llm("signals_classifier")
        try:
            structured = llm.with_structured_output(_Classification, method="json_mode")
        except Exception:  # noqa: BLE001
            structured = llm.with_structured_output(_Classification)
        logger.info("[LLM] signals_classifier: classifying region/hemisphere for dest=%r", destination)
        result = await structured.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        if not isinstance(result, _Classification):
            result = _Classification.model_validate(result)
        return _DestinationClassification(
            region=result.region, hemisphere=result.hemisphere
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "signals.llm_classifier_failed dest=%r err=%s", destination, e
        )
        return None


async def enrich_anchor_hints(signals: TravelSignals, destination: str) -> None:
    """Always runs. Populates signals.top_anchors with 5-6 canonical landmark names.

    Makes one cheap LLM call per unique destination (cached in-process).
    Bypasses the vibe-biased extraction LLM so famous anchors (Sentosa, Eiffel
    Tower, etc.) are always seeded even when the trip's first vibe is "food".
    Non-fatal on failure — pipeline continues without anchor seeds.
    """
    from pydantic import BaseModel
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.llm.factory import get_llm

    dest_key = destination.strip().lower()
    if dest_key in _LLM_ANCHOR_CACHE:
        signals.top_anchors = _LLM_ANCHOR_CACHE[dest_key]
        return

    class _AnchorList(BaseModel):
        anchors: list[str]

    system = (
        "You list the most iconic tourist landmarks for travel planning. "
        "Return only well-known, widely-recognised attractions that any first-time visitor "
        "would want to see. Exclude restaurants and food stalls unless they are globally iconic. "
        "CRITICAL: every landmark you list MUST be physically located INSIDE the destination — "
        "do not include any landmark from a neighbouring city, district, or state. "
        "Output JSON only with key 'anchors' as an array of 5-6 place name strings in English."
    )
    user = (
        f"List the 5-6 most iconic must-visit tourist landmarks that are "
        f"physically located WITHIN '{destination}' (not in nearby regions). "
        "Include famous temples, museums, natural landmarks, theme parks, historic sites, "
        "and architectural icons. Use their common English names."
    )
    try:
        llm = get_llm("signals_classifier")
        try:
            structured = llm.with_structured_output(_AnchorList, method="json_mode")
        except Exception:  # noqa: BLE001
            structured = llm.with_structured_output(_AnchorList)
        logger.info("[LLM] anchor_hints: fetching 5-6 landmarks for dest=%r", destination)
        result = await structured.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        if not isinstance(result, _AnchorList):
            result = _AnchorList.model_validate(result)
        anchors = [str(a).strip() for a in result.anchors if a][:6]

        # Geography post-filter: drop anchors the LLM commonly hallucinates
        # as belonging to a destination when they're in a neighbouring region.
        excl_lower: list[str] = []
        for kw, excl_terms in _ANCHOR_GEO_EXCLUSIONS.items():
            if kw in dest_key:
                excl_lower.extend(t.lower() for t in excl_terms)
        if excl_lower:
            before = len(anchors)
            anchors = [a for a in anchors if not any(ex in a.lower() for ex in excl_lower)]
            dropped = before - len(anchors)
            if dropped:
                logger.info(
                    "signals.anchor_hints.geo_filter dest=%r dropped=%d",
                    destination, dropped,
                )

        if anchors:
            _LLM_ANCHOR_CACHE[dest_key] = anchors
            signals.top_anchors = anchors
            logger.info("signals.anchor_hints dest=%r anchors=%r", destination, anchors)
    except Exception:  # noqa: BLE001
        logger.exception("signals.anchor_hints.failed dest=%r", destination)


async def enrich_signals_with_llm(
    signals: TravelSignals, trip_params: TripParams
) -> TravelSignals:
    """If `signals.region` is "unknown", call the LLM classifier to fill in
    region + season + warnings + query_modifiers correctly.

    No-op when region is already known. Cached in-process so repeated runs
    on the same destination don't re-pay the LLM cost. Failures degrade
    silently — returns the original signals.
    """
    if signals.region != "unknown":
        return signals

    dest_key = trip_params.destination.strip().lower()
    if not dest_key:
        return signals

    classification = _LLM_REGION_CACHE.get(dest_key)
    if classification is None:
        classification = await _classify_destination_via_llm(trip_params.destination)
        if classification is None:
            return signals
        _LLM_REGION_CACHE[dest_key] = classification

    month = _midpoint_month(trip_params.date_from, trip_params.date_to)
    season, weather_hint, season_modifiers = _infer_season_with_hemisphere(
        month, classification.region, dest_key, classification.hemisphere
    )

    festivals = _find_active_festivals(
        dest_key, trip_params.date_from, trip_params.date_to
    )
    crowd_level = _crowd_level(season, festivals)
    query_modifiers = _build_query_modifiers(
        season_modifiers, season, crowd_level, festivals, trip_params.vibes
    )
    warnings = _build_warnings(season, classification.region, dest_key, weather_hint)
    seasonal_tips = _build_seasonal_tips(season, classification.region, month, dest_key)
    currency_hint = _currency_hint(classification.region, dest_key)

    logger.info(
        "signals.llm_enriched dest=%r region=%s hemisphere=%s season=%s",
        trip_params.destination,
        classification.region,
        classification.hemisphere,
        season,
    )

    return TravelSignals(
        region=classification.region,
        season=season,
        weather_hint=weather_hint,
        is_festival_window=signals.is_festival_window,
        festival_name=signals.festival_name,
        active_festivals=signals.active_festivals,
        crowd_level=crowd_level,
        budget_tier=signals.budget_tier,
        pace_density=signals.pace_density,
        vibe_source_weights=signals.vibe_source_weights,
        query_modifiers=query_modifiers,
        warnings=warnings,
        seasonal_tips=seasonal_tips,
        currency_hint=currency_hint,
    )
