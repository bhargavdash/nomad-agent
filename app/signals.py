"""SignalExtractor — pure-Python derivation of travel signals from trip params.

No LLM call. Deterministic. Runs once at the start of every pipeline.
The output (`TravelSignals`) is consumed by every downstream agent to
shape search queries and weighting.

Matches section 3 of AI_INTEGRATION_PLAN.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.schemas import TripParams


@dataclass
class TravelSignals:
    season: str  # "winter" | "spring" | "summer" | "monsoon" | "autumn"
    is_festival_window: bool
    festival_name: str | None
    crowd_level: str  # "low" | "moderate" | "peak"
    weather_hint: str | None
    budget_tier: str  # "shoestring" | "mid" | "premium" | "luxury"
    pace_density: int  # stops per day: 3 | 4 | 5
    vibe_source_weights: dict = field(default_factory=dict)
    query_modifiers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lookup tables (intentionally small — placeholder MVP coverage).
# ---------------------------------------------------------------------------

_BUDGET_TIER = {
    "$": "shoestring",
    "$$": "mid",
    "$$$": "premium",
    "$$$$": "luxury",
}

_PACE_DENSITY = {
    "Slow & Soulful": 3,
    "Balanced": 4,
    "Action-Packed": 5,
}

# Indian / SE Asian destinations where Jun–Sep means monsoon.
_MONSOON_KEYWORDS = (
    "india",
    "goa",
    "kerala",
    "mumbai",
    "bangalore",
    "chennai",
    "thailand",
    "bangkok",
    "phuket",
    "vietnam",
    "indonesia",
    "bali",
    "philippines",
    "sri lanka",
    "cambodia",
    "laos",
    "myanmar",
)

# Destinations that are peak-season in winter.
_WINTER_PEAK_KEYWORDS = ("goa", "kerala", "rajasthan")

# TODO: expand the lookup tables below.
#  - Festivals: Holi (Mar, anywhere India), Onam (Aug/Sep, Kerala),
#    Pushkar Camel Fair (Nov, Pushkar/Rajasthan), Chinese New Year (Jan/Feb, China/SG/HK),
#    Songkran (Apr, Thailand), Oktoberfest (Sep/Oct, Munich), La Tomatina (Aug, Buñol),
#    Carnival (Feb, Rio), Cherry Blossom (Mar/Apr, Japan).
#  - Destination-specific seasons: Ladakh (snow Nov–Apr), European Alps,
#    Patagonia (reverse seasons), Caribbean hurricane window (Jun–Nov), etc.
#  - Crowd-level lookup per destination × month.
#  - Weather hints: heatwave (Delhi May/Jun), typhoon (Philippines Jul–Oct),
#    wildfire (California Jul–Oct).


def _infer_season(month: int | None, destination_lower: str) -> tuple[str, str | None, list[str]]:
    """Return (season, weather_hint, query_modifiers)."""
    if month is None:
        return "unknown", None, []

    # Monsoon: June–September in India / SE Asia.
    if month in (6, 7, 8, 9) and any(k in destination_lower for k in _MONSOON_KEYWORDS):
        return "monsoon", "monsoon-flooding-risk", ["monsoon", "indoor activities", "waterfalls"]

    # Winter: Dec–Feb (northern hemisphere assumption — TODO: flip for southern).
    if month in (12, 1, 2):
        weather_hint = None
        modifiers = ["winter", "cozy"]
        if "ladakh" in destination_lower:
            weather_hint = "snow-pass-closures"
            modifiers.append("snow")
        return "winter", weather_hint, modifiers

    # Spring: Mar–May.
    if month in (3, 4, 5):
        return "spring", None, ["spring"]

    # Summer: Jun–Aug for non-monsoon destinations.
    if month in (6, 7, 8):
        return "summer", None, ["summer"]

    # Autumn: Sep–Nov fallback.
    return "autumn", None, ["autumn"]


def _detect_festival(month: int | None, destination_lower: str) -> tuple[bool, str | None]:
    """Detect festival windows. MVP: Diwali (Oct/Nov) in India.

    TODO: expand to a full lookup table (festival_name × month × destination_keywords).
    """
    if month is None:
        return False, None

    # Diwali: late October / early November, primarily India / Jaipur / Delhi.
    diwali_destinations = ("jaipur", "delhi", "varanasi", "udaipur", "rajasthan", "india")
    if month in (10, 11) and any(k in destination_lower for k in diwali_destinations):
        return True, "Diwali"

    return False, None


def _crowd_level(month: int | None, destination_lower: str) -> str:
    if month is None:
        return "moderate"
    if month in (12, 1) and any(k in destination_lower for k in _WINTER_PEAK_KEYWORDS):
        return "peak"
    return "moderate"


def _vibe_weights(vibes: list[str]) -> dict:
    vibes_lower = {v.lower() for v in vibes}
    if "hidden gems" in vibes_lower or "off the beaten path" in vibes_lower:
        return {"reddit": 0.5, "youtube": 0.4, "blog": 0.1}
    if "iconic" in vibes_lower or "first time" in vibes_lower:
        return {"reddit": 0.2, "youtube": 0.3, "blog": 0.5}
    return {"reddit": 0.34, "youtube": 0.33, "blog": 0.33}


def extract_signals(trip_params: TripParams) -> TravelSignals:
    """Derive deterministic signals from trip parameters. No LLM."""
    month: int | None = None
    if trip_params.date_from:
        try:
            month = date.fromisoformat(trip_params.date_from).month
        except ValueError:
            month = None

    destination_lower = trip_params.destination.lower()

    season, weather_hint, query_modifiers = _infer_season(month, destination_lower)

    is_festival, festival_name = _detect_festival(month, destination_lower)
    if is_festival and festival_name:
        query_modifiers.append(festival_name)

    crowd_level = _crowd_level(month, destination_lower)
    budget_tier = _BUDGET_TIER[trip_params.budget]
    pace_density = _PACE_DENSITY[trip_params.pace]
    vibe_source_weights = _vibe_weights(trip_params.vibes)

    # Mix in vibe keywords as query modifiers (cheap way to bias search).
    for v in trip_params.vibes:
        if v and v.lower() not in {q.lower() for q in query_modifiers}:
            query_modifiers.append(v)

    return TravelSignals(
        season=season,
        is_festival_window=is_festival,
        festival_name=festival_name,
        crowd_level=crowd_level,
        weather_hint=weather_hint,
        budget_tier=budget_tier,
        pace_density=pace_density,
        vibe_source_weights=vibe_source_weights,
        query_modifiers=query_modifiers,
    )
