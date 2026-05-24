"""Pydantic schemas mirroring the Zod schemas on the Node side.

Matches section 5 of AI_INTEGRATION_PLAN.md exactly. Field names are the
contract between the Node and Python services — do not rename.
"""

from typing import Literal

from pydantic import BaseModel, Field

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
    travelers: Literal["1", "2", "3+", "large"] = "2"
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
    emoji: str = Field(..., min_length=1, max_length=4)
    stats_places: int
    stats_tips: int
    stats_photo_stops: int
    discoveries: list[ResearchDiscovery] = Field(..., min_length=3, max_length=12)
    days: list[AIDay] = Field(..., min_length=1)
