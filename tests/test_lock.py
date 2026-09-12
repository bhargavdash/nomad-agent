"""FI-2 locked-stop merge — pure unit tests, no I/O."""

from __future__ import annotations

from app.lock import LockedStop, apply_locked_stops
from app.schemas import AIDay, AIItinerary, AIStop, ResearchDiscovery


def _stop(name: str, sort_order: int, *, locked: bool = False) -> AIStop:
    return AIStop(
        sortOrder=sort_order,
        time="9:00",
        ampm="AM",
        duration="1h",
        name=name,
        description=f"{name} desc",
        source="maps",
        tags=["walk"],
        locked=locked,
    )


def _itin(*names: str) -> AIItinerary:
    discoveries = [
        ResearchDiscovery(id=f"d-{i}", title=f"D{i}", body="b", tags=["a"], source="blog")
        for i in range(1, 4)
    ]
    return AIItinerary(
        stats_places=len(names),
        stats_tips=0,
        stats_photo_stops=0,
        discoveries=discoveries,
        days=[
            AIDay(
                dayNumber=1,
                city="Goa",
                title="Day 1",
                description="Arrival",
                highlights=["one", "two"],
                stops=[_stop(n, i) for i, n in enumerate(names, start=1)],
            )
        ],
    )


def _locked(name: str, *, day: int = 1) -> LockedStop:
    return LockedStop(
        id="lock-1",
        day_number=day,
        name=name,
        time="4:00",
        ampm="PM",
        duration="2h",
        description="user-edited locked copy",
        source="blog",
        tags=["keep"],
        sort_order=1,
    )


def test_apply_locked_empty_is_noop() -> None:
    itin = _itin("A", "B")
    out = apply_locked_stops(itin, [])
    assert [s.name for s in out.days[0].stops] == ["A", "B"]
    assert all(not s.locked for s in out.days[0].stops)


def test_apply_locked_replaces_same_name_and_keeps_user_fields() -> None:
    out = apply_locked_stops(_itin("Baga Beach", "Market"), [_locked("Baga Beach")])
    baga = next(s for s in out.days[0].stops if s.name == "Baga Beach")
    assert baga.locked is True
    assert baga.time == "4:00"
    assert baga.ampm == "PM"
    assert baga.description == "user-edited locked copy"
    assert [s.name for s in out.days[0].stops] == ["Baga Beach", "Market"]


def test_apply_locked_appends_when_draft_dropped_the_stop() -> None:
    out = apply_locked_stops(_itin("Market", "Fort"), [_locked("Baga Beach")])
    names = [s.name for s in out.days[0].stops]
    assert names[-1] == "Baga Beach"
    assert out.days[0].stops[-1].locked is True
    assert out.days[0].stops[-1].sortOrder == len(names)
