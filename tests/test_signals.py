"""Unit tests for the SignalExtractor (no LLM, no network)."""

from __future__ import annotations

from app.schemas import TripParams
from app.signals import extract_signals


def _trip(**overrides) -> TripParams:
    base = dict(
        trip_id="t1",
        user_id="u1",
        destination="Goa, India",
        date_from="2026-06-15",
        date_to="2026-06-22",
        duration_days=7,
        travelers="2",
        vibes=["beaches"],
        accommodation="Hotel",
        pace="Balanced",
        budget="$$",
        preferences=None,
    )
    base.update(overrides)
    return TripParams(**base)


def test_goa_in_june_is_monsoon() -> None:
    signals = extract_signals(_trip(destination="Goa, India", date_from="2026-06-15"))
    assert signals.season == "monsoon"
    assert signals.weather_hint == "monsoon-flooding-risk"
    assert "monsoon" in signals.query_modifiers


def test_jaipur_late_october_is_festival_window() -> None:
    signals = extract_signals(
        _trip(destination="Jaipur, India", date_from="2026-10-28", vibes=["culture"])
    )
    assert signals.is_festival_window is True
    assert signals.festival_name == "Diwali"
    assert "Diwali" in signals.query_modifiers


def test_slow_and_soulful_pace_density_is_three() -> None:
    signals = extract_signals(_trip(pace="Slow & Soulful"))
    assert signals.pace_density == 3
