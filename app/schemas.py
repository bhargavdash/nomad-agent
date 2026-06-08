"""Pydantic schemas mirroring the Zod schemas on the Node side.

Matches section 5 of AI_INTEGRATION_PLAN.md exactly. Field names are the
contract between the Node and Python services — do not rename.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

SourceType = Literal["youtube", "reddit", "blog", "maps"]
# Note: "maps" is the wire value for synthesizer-padded anchor stops (NOT a
# Google Maps fact). The name is preserved here only because it is mirrored
# in nomad-api's Zod schema and possibly the Postgres check constraint;
# renaming requires a coordinated Node-side change.


class TripParams(BaseModel):
    """Input from Node side."""

    trip_id: str
    user_id: str
    destination: str
    date_from: str | None = None
    date_to: str | None = None
    duration_days: int = 7
    # Stringified integer count of travellers ("1".."10"). Stored verbatim as a
    # string to keep the Node↔Python wire format and the Prisma `String?` column
    # unchanged. The pattern rejects non-numeric input (e.g. "ten") while
    # accepting any positive integer; the 1-10 ceiling is enforced UI-side and
    # by the Zod schema in nomad-api.
    travelers: Annotated[str, StringConstraints(pattern=r"^\d+$")] = "2"
    vibes: list[str] = Field(default_factory=list)
    accommodation: str = "Hotel"
    pace: Literal["Slow & Soulful", "Balanced", "Action-Packed"] = "Balanced"
    budget: Literal["Low", "Medium", "High", "Very-High"] = "Medium"
    preferences: str | None = None


class ResearchDiscovery(BaseModel):
    id: str
    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    tags: list[str] = Field(..., min_length=1, max_length=3)
    source: SourceType


class AIStop(BaseModel):
    sortOrder: int = Field(..., ge=1)
    time: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    ampm: Literal["AM", "PM"]
    duration: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    source: SourceType
    tags: list[str] = Field(..., min_length=1, max_length=4)


class AIDay(BaseModel):
    dayNumber: int = Field(..., ge=1)
    city: str
    title: str
    description: str
    highlights: list[str] = Field(..., min_length=2, max_length=5)
    stops: list[AIStop] = Field(..., min_length=2, max_length=6)


class AIItinerary(BaseModel):
    stats_places: int
    stats_tips: int
    stats_photo_stops: int
    # Trip-level planning surface (Tier 2). All optional so the skeleton fallback
    # and back-compat stay valid. Mirror these in nomad-api's Prisma `Trip` model
    # (snake_case columns) and nomad-web's `TripSummary` type.
    route_summary: str | None = None  # city circuit + day allocation, or single-city arc
    transport_strategy: str | None = None  # how to move between/within cities
    seasonal_tips: list[str] = Field(default_factory=list)  # from signals (deterministic)
    stay_by_city: dict[str, str] = Field(default_factory=dict)  # {city: "area + tier"}
    budget_estimate: str | None = None  # rough cost blurb
    discoveries: list[ResearchDiscovery] = Field(..., min_length=3, max_length=12)
    days: list[AIDay] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# SA-8 — Trending destinations (single LLM call, season-cached)
# ---------------------------------------------------------------------------


class TrendingDest(BaseModel):
    name: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    duration: str = Field(..., min_length=1)  # e.g. "5-7 days"
    blurb: str = Field(..., min_length=1, max_length=140)
    vibe_tags: list[str] = Field(..., min_length=1, max_length=3)
    # Resolved + self-hosted at trending-refresh time (see app/images.py), then
    # served verbatim by nomad-api. camelCase matches nomad-api's TrendingDest
    # type and the frontend's `dest.imageUrl`. None until resolved / no photo.
    # The LLM isn't prompted for it; resolution overwrites it unconditionally.
    imageUrl: str | None = None


class TrendingPayload(BaseModel):
    """Season-keyed payload written to nomad-api's `trending_cache` table.

    `india` and `international` must each contain exactly 10 destinations.
    The Node API reads this JSON and ships it to the frontend untouched.
    """

    season: Literal["summer", "monsoon", "post-monsoon", "winter"]
    year: int = Field(..., ge=2020, le=2100)
    india: list[TrendingDest] = Field(..., min_length=10, max_length=10)
    international: list[TrendingDest] = Field(..., min_length=10, max_length=10)
