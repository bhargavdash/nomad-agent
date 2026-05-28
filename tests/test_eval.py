"""Tests for the heuristic eval scorer (app/eval.py). Pure, no tokens/network."""

from __future__ import annotations

from app.eval import score_itinerary
from app.schemas import AIDay, AIItinerary, AIStop, ResearchDiscovery, TripParams
from app.signals import extract_signals


def _trip(**ov) -> TripParams:
    base = dict(
        trip_id="t1",
        user_id="u1",
        destination="Rajasthan, India",
        date_from="2026-12-20",
        date_to="2026-12-22",
        duration_days=2,
        travelers="2",
        vibes=["heritage"],
        accommodation="Heritage Haveli",
        pace="Balanced",
        budget="Medium",
        preferences=None,
    )
    base.update(ov)
    return TripParams(**base)


def _stop(name, t="9:00", ap="AM", src="blog", desc="A real, specific stop.") -> AIStop:
    return AIStop(
        sortOrder=1,
        time=t,
        ampm=ap,
        duration="1h",
        name=name,
        description=desc,
        source=src,
        tags=["📍"],  # type: ignore[arg-type]
    )


def _disc(i: int) -> ResearchDiscovery:
    return ResearchDiscovery(id=f"d{i}", title=f"P{i}", body="b", tags=["t"], source="blog")


def _good_itinerary() -> AIItinerary:
    days = [
        AIDay(
            dayNumber=1,
            city="Jaipur",
            title="Pink City",
            description="Forts and bazaars.",
            highlights=["Hawa Mahal", "Dal baati at LMB"],
            stops=[
                _stop("Hawa Mahal", "8:30", "AM"),
                _stop("City Palace", "11:00", "AM"),
                _stop("Nahargarh Fort", "5:00", "PM"),
            ],
        ),
        AIDay(
            dayNumber=2,
            city="Jaipur",
            title="Markets",
            description="Bazaar crawl.",
            highlights=["Bandhani at Bapu Bazaar", "Amber Fort"],
            stops=[
                _stop("Amber Fort", "8:30", "AM"),
                _stop("Bapu Bazaar", "12:00", "PM"),
                _stop("Chokhi Dhani", "7:00", "PM"),
            ],
        ),
    ]
    return AIItinerary(
        stats_places=6,
        stats_tips=1,
        stats_photo_stops=2,
        route_summary="Jaipur (2)",
        transport_strategy="Cabs within the city.",
        seasonal_tips=["Pack layers — Dec nights are cold."],
        stay_by_city={"Jaipur": "Bani Park heritage haveli"},
        budget_estimate="₹40,000–60,000 for two",
        discoveries=[_disc(i) for i in range(3)],
        days=days,
    )


def test_good_itinerary_scores_high() -> None:
    trip = _trip()
    res = score_itinerary(_good_itinerary(), trip, extract_signals(trip))
    assert res["score"] >= 90
    assert res["checks"]["currency_ok"] is True
    assert res["checks"]["has_route_summary"] is True
    assert res["checks"]["chronology_ok"] is True


def test_bad_itinerary_scores_low() -> None:
    trip = _trip()
    days = [
        AIDay(
            dayNumber=1,
            city="Jaipur",
            title="Day 1",
            description="A stunning, must-visit day of vibrant culture.",  # banned words
            highlights=["a", "b"],
            stops=[
                _stop("Lunch in Jaipur", "1:00", "PM", src="maps"),  # filler
                _stop(
                    "Morning coffee in Jaipur", "9:00", "AM", src="maps"
                ),  # filler + out of order
            ],
        ),
    ]
    bad = AIItinerary(
        stats_places=0,
        stats_tips=0,
        stats_photo_stops=0,
        route_summary="",  # missing
        budget_estimate="",  # missing
        discoveries=[_disc(i) for i in range(3)],
        days=days,
    )
    res = score_itinerary(bad, trip, extract_signals(trip))
    assert res["score"] <= 40
    assert res["checks"]["has_route_summary"] is False
    assert res["checks"]["no_banned_words"] is False
    assert res["checks"]["filler_under_40pct"] is False
    assert res["checks"]["chronology_ok"] is False


def test_currency_check_detects_wrong_currency() -> None:
    trip = _trip(destination="Rajasthan, India")  # expects ₹
    itin = _good_itinerary()
    itin.budget_estimate = "$1,500 for two"  # wrong currency for India
    res = score_itinerary(itin, trip, extract_signals(trip))
    assert res["checks"]["currency_ok"] is False
