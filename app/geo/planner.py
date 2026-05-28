"""Build a geo brief for a trip: a verified city circuit + distances + sun times.

Flow (all best-effort, graceful on any failure):
  1. A cheap LLM call picks the city circuit (ordered names + day counts).
  2. Geocode each city via Nominatim (with destination context for disambig).
  3. Reorder greedily (nearest-neighbour from the entry city) to kill backtracks.
  4. Compute per-leg road distance + drive-time, and per-city sunrise/sunset.

The synthesizer narrates against this brief — real distances/sun-times flow into
its existing prose fields. If anything fails, `build_geo_brief` returns an empty
brief and the synthesizer behaves exactly as before.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field

from app.geo.distance import road_km, drive_time_hint
from app.geo.sun import sun_times
from app.schemas import TripParams
from app.signals import TravelSignals
from app.tools.geocode import geocode

logger = logging.getLogger(__name__)

MAX_CITIES = 8  # cap the circuit; longer trips still group days within cities


# ---------------------------------------------------------------------------
# Timezone offsets (for local sunrise/sunset). India is exact (single, DST-free
# zone). Others are coarse standard-time offsets — only mapped where we're
# confident; unknown → None → sun times skipped (never show an absurd time).
# ---------------------------------------------------------------------------

# IANA timezone names (DST-aware). We map to a zone, then ask zoneinfo for the
# offset on the *trip date* — so European summer (CEST) etc. come out right,
# fixing the "Paris sunrise 1h early" blemish. India is single-zone/no-DST.
_TZ_NAME_OVERRIDES: dict[str, str] = {
    "thailand": "Asia/Bangkok",
    "bangkok": "Asia/Bangkok",
    "phuket": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh",
    "hanoi": "Asia/Ho_Chi_Minh",
    "ho chi minh": "Asia/Ho_Chi_Minh",
    "indonesia": "Asia/Jakarta",
    "jakarta": "Asia/Jakarta",
    "bali": "Asia/Makassar",
    "singapore": "Asia/Singapore",
    "malaysia": "Asia/Kuala_Lumpur",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "japan": "Asia/Tokyo",
    "tokyo": "Asia/Tokyo",
    "philippines": "Asia/Manila",
    "manila": "Asia/Manila",
    "sri lanka": "Asia/Colombo",
    "dubai": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "uk": "Europe/London",
    "london": "Europe/London",
    "england": "Europe/London",
    "scotland": "Europe/London",
    "france": "Europe/Paris",
    "paris": "Europe/Paris",
    "italy": "Europe/Rome",
    "rome": "Europe/Rome",
    "venice": "Europe/Rome",
    "spain": "Europe/Madrid",
    "madrid": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "germany": "Europe/Berlin",
    "berlin": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "switzerland": "Europe/Zurich",
    "greece": "Europe/Athens",
    "athens": "Europe/Athens",
    "portugal": "Europe/Lisbon",
    "lisbon": "Europe/Lisbon",
    "netherlands": "Europe/Amsterdam",
    "amsterdam": "Europe/Amsterdam",
    "austria": "Europe/Vienna",
    "vienna": "Europe/Vienna",
    "prague": "Europe/Prague",
    "budapest": "Europe/Budapest",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "usa": "America/New_York",
    "united states": "America/New_York",
    "boston": "America/New_York",
    "miami": "America/New_York",
    "chicago": "America/Chicago",
    "los angeles": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "las vegas": "America/Los_Angeles",
    "canada": "America/Toronto",
    "toronto": "America/Toronto",
    "vancouver": "America/Vancouver",
    "montreal": "America/Toronto",
    "mexico": "America/Mexico_City",
    "cancun": "America/Cancun",
    "australia": "Australia/Sydney",
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "new zealand": "Pacific/Auckland",
    "auckland": "Pacific/Auckland",
    "queenstown": "Pacific/Auckland",
}
_REGION_TZ_DEFAULT: dict[str, str] = {
    "india": "Asia/Kolkata",
    "southeast_asia": "Asia/Bangkok",
    "europe": "Europe/Paris",
    "north_america": "America/New_York",
    "oceania": "Australia/Sydney",
}


def _tz_name(region: str, destination_lower: str) -> str | None:
    """IANA timezone for a destination. Specific keyword wins over region default;
    None when unknown so sun times are skipped (never an absurd time)."""
    for keyword, tz in _TZ_NAME_OVERRIDES.items():
        if keyword in destination_lower:
            return tz
    return _REGION_TZ_DEFAULT.get(region)


def _offset_hours(tz_name: str, d: date) -> float | None:
    """DST-aware UTC offset (hours) for a timezone on a given date."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Noon avoids DST-transition edge cases at midnight.
        offset = datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo(tz_name)).utcoffset()
        return offset.total_seconds() / 3600 if offset is not None else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Output shapes
# ---------------------------------------------------------------------------


@dataclass
class GeoLeg:
    from_city: str
    to_city: str
    km: int
    drive_hint: str


@dataclass
class GeoBrief:
    ordered_cities: list[str] = field(default_factory=list)
    legs: list[GeoLeg] = field(default_factory=list)
    sun: dict[str, tuple[str, str]] = field(default_factory=dict)  # city -> (sunrise, sunset)
    reordered: bool = False  # True if we improved on the LLM's order

    def is_empty(self) -> bool:
        return not self.ordered_cities

    def to_prompt_block(self) -> str:
        """Render the brief for the synthesizer prompt, or '' when empty."""
        if self.is_empty():
            return ""
        lines = ["=== Geography (verified — use this exact city order) ==="]
        lines.append("City order: " + " → ".join(self.ordered_cities))
        if self.legs:
            lines.append("Inter-city legs (by road, approximate):")
            for leg in self.legs:
                lines.append(f"  {leg.from_city} → {leg.to_city}: ~{leg.km} km ({leg.drive_hint})")
        if self.sun:
            lines.append("Sunrise / sunset (local, for time-of-day hooks):")
            for city, (sr, ss) in self.sun.items():
                lines.append(f"  {city}: sunrise {sr}, sunset {ss}")
        lines.append(
            "Visit cities in the order above (it minimises backtracking). Cite the "
            "real distances/drive-times in transport_strategy, and use the sun "
            "times for sunrise/sunset hooks. Do NOT place a landmark in a city it "
            "isn't in."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 1 — LLM picks the city circuit
# ---------------------------------------------------------------------------


class _City(BaseModel):
    name: str = Field(..., min_length=1)
    days: int = Field(default=1, ge=1)


class _CityPick(BaseModel):
    cities: list[_City] = Field(default_factory=list)


_PICK_SYSTEM = (
    "You plan the CITY CIRCUIT for a trip — nothing else. Given a destination, "
    "trip length, and interests, return the ordered list of cities/towns to base "
    "in, each with how many nights it gets. Rules: only real places inside the "
    "destination; order them as a sensible route; major cities get 2-3 days, "
    "smaller stops 1; total days should roughly match the trip length. If the "
    "destination is a single city, return just that one city. Output JSON only: "
    '{"cities": [{"name": "...", "days": N}, ...]}.'
)


async def _pick_cities(trip_params: TripParams, signals: TravelSignals) -> list[str]:
    from app.llm.factory import get_structured_llm

    from langchain_core.messages import HumanMessage, SystemMessage

    user = (
        f"Destination: {trip_params.destination}\n"
        f"Trip length: {trip_params.duration_days} days\n"
        f"Vibes: {', '.join(trip_params.vibes) if trip_params.vibes else '—'}\n"
        f"Preferences: {trip_params.preferences or '—'}\n\n"
        "Return the ordered city circuit with nights per city."
    )
    try:
        structured = get_structured_llm("geo_planner", _CityPick, method="json_mode")
        result = await structured.ainvoke(
            [SystemMessage(content=_PICK_SYSTEM), HumanMessage(content=user)]
        )
        if not isinstance(result, _CityPick):
            result = _CityPick.model_validate(result)
        seen: set[str] = set()
        cities: list[str] = []
        for c in result.cities:
            name = c.name.strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                cities.append(name)
        return cities[:MAX_CITIES]
    except Exception as e:  # noqa: BLE001
        logger.warning("geo.pick_cities_failed dest=%r err=%s", trip_params.destination, e)
        return []


# ---------------------------------------------------------------------------
# Stage 2-4 — geocode, order, legs, sun
# ---------------------------------------------------------------------------


def _nearest_neighbour_order(
    cities: list[str], coords: dict[str, tuple[float, float]]
) -> tuple[list[str], bool]:
    """Greedy NN tour starting from the first city. Returns (order, improved?).

    `improved` is True when the NN total distance beats the input order's by a
    meaningful margin — the signal that the LLM's order backtracked.
    """
    if len(cities) <= 2:
        return cities, False

    def total(seq: list[str]) -> float:
        return sum(road_km(*coords[seq[i]], *coords[seq[i + 1]]) for i in range(len(seq) - 1))

    start = cities[0]
    remaining = [c for c in cities if c != start]
    order = [start]
    while remaining:
        last = order[-1]
        nxt = min(remaining, key=lambda c: road_km(*coords[last], *coords[c]))
        order.append(nxt)
        remaining.remove(nxt)

    # Only adopt the NN order if it's a real improvement (> 10% shorter).
    improved = total(order) < total(cities) * 0.9
    return (order, True) if improved else (cities, False)


async def build_geo_brief(trip_params: TripParams, signals: TravelSignals) -> GeoBrief:
    """Build the geo brief. Always returns a GeoBrief (empty on any failure)."""
    try:
        cities = await _pick_cities(trip_params, signals)
        if not cities:
            return GeoBrief()

        # Geocode each city, biasing the query with the destination for
        # disambiguation (e.g. "Jodhpur, Rajasthan, India").
        dest = trip_params.destination
        coords: dict[str, tuple[float, float]] = {}
        for city in cities:
            query = city if dest.lower() in city.lower() else f"{city}, {dest}"
            latlng = await geocode(query)
            if latlng is None:
                latlng = await geocode(city)  # retry without context
            if latlng is not None:
                coords[city] = latlng

        geocoded = [c for c in cities if c in coords]
        if not geocoded:
            logger.info("geo.brief: no cities geocoded for %r", dest)
            return GeoBrief()

        ordered, reordered = _nearest_neighbour_order(geocoded, coords)

        legs: list[GeoLeg] = []
        for i in range(len(ordered) - 1):
            a, b = ordered[i], ordered[i + 1]
            km = road_km(*coords[a], *coords[b])
            legs.append(GeoLeg(a, b, km, drive_time_hint(km)))

        # Sun times for the trip start date (close enough across a short trip).
        # Offset is DST-aware (computed from the IANA zone on the trip date).
        sun: dict[str, tuple[str, str]] = {}
        tz_name = _tz_name(signals.region, dest.lower())
        if tz_name is not None:
            try:
                d0 = (
                    date.fromisoformat(trip_params.date_from)
                    if trip_params.date_from
                    else date.today()
                )
            except ValueError:
                d0 = date.today()
            tz_offset = _offset_hours(tz_name, d0)
            if tz_offset is not None:
                for city in ordered:
                    lat, lng = coords[city]
                    times = sun_times(d0, lat, lng, tz_offset)
                    if times is not None:
                        sun[city] = times

        logger.info(
            "geo.brief dest=%r cities=%d legs=%d sun=%d reordered=%s",
            dest,
            len(ordered),
            len(legs),
            len(sun),
            reordered,
        )
        return GeoBrief(ordered_cities=ordered, legs=legs, sun=sun, reordered=reordered)
    except Exception:  # noqa: BLE001
        logger.exception("geo.build_geo_brief failed dest=%r", trip_params.destination)
        return GeoBrief()
