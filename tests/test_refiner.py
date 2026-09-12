"""FI-1 refiner — wire coerce + day merge, no LLM."""

from __future__ import annotations

from app.agents.refiner import _RefineDay, _RefineDraft, _RefineStop, itinerary_from_wire, merge_refine_draft
from app.schemas import AIStop


def _wire_stop(name: str, *, locked: bool = False, sort: int = 1) -> dict:
    return {
        "sortOrder": sort,
        "time": "9:00",
        "ampm": "AM",
        "duration": "1h",
        "name": name,
        "description": f"{name} desc",
        "source": "maps",
        "tags": ["walk"],
        "locked": locked,
    }


def test_itinerary_from_wire_pads_thin_days_and_keeps_locks() -> None:
    raw = {
        "destination": "Goa",
        "stats_places": 1,
        "days": [
            {
                "dayNumber": 1,
                "city": "Panaji",
                "title": "Arrival",
                "description": "Land",
                "highlights": ["one"],
                "stops": [_wire_stop("Cafe", locked=True)],
            }
        ],
        "discoveries": [],
    }
    itinerary = itinerary_from_wire(raw)
    assert len(itinerary.days) == 1
    assert len(itinerary.days[0].stops) >= 2
    assert itinerary.days[0].stops[0].locked is True
    assert itinerary.days[0].stops[0].name == "Cafe"
    assert len(itinerary.discoveries) >= 3
    assert len(itinerary.days[0].highlights) >= 2


def test_merge_refine_draft_keeps_unreturned_days() -> None:
    raw = {
        "days": [
            {
                "dayNumber": 1,
                "city": "Panaji",
                "title": "Day 1",
                "description": "A",
                "highlights": ["a", "b"],
                "stops": [_wire_stop("Cafe", sort=1), _wire_stop("Walk", sort=2)],
            },
            {
                "dayNumber": 2,
                "city": "Calangute",
                "title": "Day 2",
                "description": "B",
                "highlights": ["c", "d"],
                "stops": [_wire_stop("Beach", sort=1), _wire_stop("Dinner", sort=2)],
            },
        ]
    }
    original = itinerary_from_wire(raw)
    draft = _RefineDraft(
        days=[
            _RefineDay(
                dayNumber=2,
                city="Calangute",
                title="Food day",
                description="More food",
                highlights=["food", "night"],
                stops=[
                    _RefineStop(name="Beach", description="keep", time="10:00"),
                    _RefineStop(name="New Kitchen", description="swap", time="7:00", ampm="PM"),
                ],
            )
        ]
    )
    merged = merge_refine_draft(original, draft)
    assert merged.days[0].title == "Day 1"
    assert merged.days[0].stops[0].name == "Cafe"
    assert merged.days[1].title == "Food day"
    names = [s.name for s in merged.days[1].stops]
    assert "New Kitchen" in names
    assert "Beach" in names
    assert "Dinner" not in names


def test_merge_preserves_locked_flag_by_name() -> None:
    raw = {
        "days": [
            {
                "dayNumber": 1,
                "city": "Goa",
                "title": "Day 1",
                "description": "A",
                "highlights": ["a", "b"],
                "stops": [
                    _wire_stop("Baga Beach", locked=True, sort=1),
                    _wire_stop("Market", sort=2),
                ],
            }
        ]
    }
    original = itinerary_from_wire(raw)
    draft = _RefineDraft(
        days=[
            _RefineDay(
                dayNumber=1,
                city="Goa",
                title="Day 1",
                description="A",
                stops=[
                    _RefineStop(name="Baga Beach", description="llm rewrite"),
                    _RefineStop(name="Spice Farm", description="new"),
                ],
            )
        ]
    )
    merged = merge_refine_draft(original, draft)
    baga = next(s for s in merged.days[0].stops if s.name == "Baga Beach")
    assert baga.locked is True
    assert isinstance(baga, AIStop)
