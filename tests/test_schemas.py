"""Validation tests for TripParams and AIItinerary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import AIDay, AIItinerary, AIStop, ResearchDiscovery, TripParams


# ---------------------------------------------------------------------------
# TripParams
# ---------------------------------------------------------------------------


def _valid_trip_payload() -> dict:
    return {
        "trip_id": "t1",
        "user_id": "u1",
        "destination": "Goa, India",
        "date_from": "2026-06-15",
        "date_to": "2026-06-22",
        "duration_days": 7,
        "travelers": "2",
        "vibes": ["beaches"],
        "accommodation": "Boutique Villa",
        "pace": "Balanced",
        "budget": "Medium",
        "preferences": None,
    }


def test_trip_params_accepts_valid_payload() -> None:
    trip = TripParams.model_validate(_valid_trip_payload())
    assert trip.destination == "Goa, India"
    assert trip.pace == "Balanced"


def test_trip_params_uses_defaults_for_optional_fields() -> None:
    trip = TripParams(trip_id="t1", user_id="u1", destination="Goa")
    assert trip.duration_days == 7
    assert trip.travelers == "2"
    assert trip.budget == "Medium"
    assert trip.pace == "Balanced"
    assert trip.vibes == []


def test_trip_params_rejects_invalid_pace() -> None:
    payload = _valid_trip_payload()
    payload["pace"] = "Sprint"
    with pytest.raises(ValidationError):
        TripParams.model_validate(payload)


def test_trip_params_rejects_invalid_budget() -> None:
    payload = _valid_trip_payload()
    payload["budget"] = "Extreme"
    with pytest.raises(ValidationError):
        TripParams.model_validate(payload)


def test_trip_params_rejects_invalid_travelers() -> None:
    payload = _valid_trip_payload()
    payload["travelers"] = "ten"
    with pytest.raises(ValidationError):
        TripParams.model_validate(payload)


# ---------------------------------------------------------------------------
# AIItinerary
# ---------------------------------------------------------------------------


def _valid_stop(sort_order: int = 1) -> AIStop:
    return AIStop(
        sortOrder=sort_order,
        time="9:00",
        ampm="AM",
        duration="1h",
        name="Test Stop",
        description="A stop",
        source="maps",
        tags=["☕"],
    )


def _valid_day() -> AIDay:
    return AIDay(
        dayNumber=1,
        city="Goa",
        title="Day 1",
        description="Arrival",
        highlights=["one", "two"],
        stops=[_valid_stop(1), _valid_stop(2), _valid_stop(3)],
    )


def _valid_discoveries() -> list[ResearchDiscovery]:
    return [
        ResearchDiscovery(
            id=f"d-{i}",
            title=f"Disc {i}",
            body="body",
            tags=["a"],
            source="blog",
        )
        for i in range(1, 4)
    ]


def test_ai_itinerary_accepts_valid_payload() -> None:
    itin = AIItinerary(
        stats_places=3,
        stats_tips=2,
        stats_photo_stops=1,
        discoveries=_valid_discoveries(),
        days=[_valid_day()],
    )
    assert itin.days[0].dayNumber == 1
    assert len(itin.discoveries) == 3


def test_ai_stop_rejects_invalid_time_format() -> None:
    with pytest.raises(ValidationError):
        AIStop(
            sortOrder=1,
            time="9-00",  # invalid — must match \d{1,2}:\d{2}
            ampm="AM",
            duration="1h",
            name="Test Stop",
            description="A stop",
            source="maps",
            tags=["☕"],
        )


def test_ai_itinerary_rejects_too_few_stops() -> None:
    with pytest.raises(ValidationError):
        AIDay(
            dayNumber=1,
            city="Goa",
            title="Day 1",
            description="Arrival",
            highlights=["one", "two"],
            stops=[_valid_stop(1)],  # only 1 stop, min is 2
        )


def test_ai_itinerary_accepts_two_stops() -> None:
    # min_length lowered 3 → 2 so days with thin research emit fewer real
    # stops instead of being padded with filler. See BENCHMARK §8.1.
    day = AIDay(
        dayNumber=1,
        city="Goa",
        title="Day 1",
        description="Arrival",
        highlights=["one", "two"],
        stops=[_valid_stop(1), _valid_stop(2)],
    )
    assert len(day.stops) == 2


def test_ai_itinerary_rejects_too_few_discoveries() -> None:
    with pytest.raises(ValidationError):
        AIItinerary(
            stats_places=0,
            stats_tips=0,
            stats_photo_stops=0,
            discoveries=_valid_discoveries()[:2],  # only 2, min is 3
            days=[_valid_day()],
        )


def test_ai_itinerary_rejects_invalid_source() -> None:
    with pytest.raises(ValidationError):
        ResearchDiscovery(
            id="x",
            title="t",
            body="b",
            tags=["a"],
            source="instagram",  # type: ignore[arg-type]
        )
