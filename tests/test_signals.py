"""Unit tests for the SignalExtractor (no LLM, no network).

The 5 destination cases below are the contract for AI-2.
The two integration tests at the end (`differ_by_destination`, `differ_by_dates`)
are the most important — they prove the layer is doing real personalisation work,
not just returning constants dressed up as signals.
"""

from __future__ import annotations

import math

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
        budget="Medium",
        preferences=None,
    )
    base.update(overrides)
    return TripParams(**base)


# ---------------------------------------------------------------------------
# Seasonal tips (Tier 1) — deterministic practical tips, not hazards.
# ---------------------------------------------------------------------------


def test_seasonal_tips_rajasthan_december() -> None:
    # Peak winter trip → "book ahead" + "cold nights / layers".
    trip = _trip(
        destination="Rajasthan, India",
        date_from="2026-12-20",
        date_to="2026-12-31",
        vibes=["heritage"],
    )
    sig = extract_signals(trip)
    joined = " ".join(sig.seasonal_tips).lower()
    assert sig.seasonal_tips, "expected seasonal tips for a Dec Rajasthan trip"
    assert "book" in joined and ("cold" in joined or "layers" in joined)


def test_seasonal_tips_empty_for_temperate_shoulder() -> None:
    # Europe shoulder season → no peak/cold/monsoon/heat tip fires.
    trip = _trip(
        destination="Paris, France",
        date_from="2026-04-10",
        date_to="2026-04-17",
        vibes=["art"],
    )
    sig = extract_signals(trip)
    assert sig.seasonal_tips == []


def test_currency_hint_by_destination() -> None:
    assert extract_signals(_trip(destination="Rajasthan, India")).currency_hint == "INR (₹)"
    assert extract_signals(_trip(destination="Paris, France")).currency_hint == "EUR (€)"
    assert extract_signals(_trip(destination="Bangkok, Thailand")).currency_hint == "THB (฿)"
    assert extract_signals(_trip(destination="London, UK")).currency_hint == "GBP (£)"
    # Unknown / ambiguous → None (synthesizer infers from the destination).
    assert extract_signals(_trip(destination="Atlantis")).currency_hint is None


# ---------------------------------------------------------------------------
# Existing (back-compat) tests — kept passing.
# ---------------------------------------------------------------------------


def test_goa_in_june_is_monsoon() -> None:
    signals = extract_signals(_trip(destination="Goa, India", date_from="2026-06-15"))
    assert signals.season == "monsoon"
    assert signals.weather_hint == "monsoon-flooding-risk"
    assert "monsoon" in signals.query_modifiers


def test_jaipur_late_october_is_festival_window() -> None:
    signals = extract_signals(
        _trip(
            destination="Jaipur, India",
            date_from="2026-10-28",
            date_to="2026-11-04",
            vibes=["culture"],
        )
    )
    assert signals.is_festival_window is True
    assert signals.festival_name == "Diwali"
    assert "Diwali" in signals.active_festivals
    assert any("Diwali" in m for m in signals.query_modifiers)


def test_slow_and_soulful_pace_density_is_three() -> None:
    signals = extract_signals(_trip(pace="Slow & Soulful"))
    assert signals.pace_density == 3


# ---------------------------------------------------------------------------
# AI-2 contract tests — 5 destinations.
# ---------------------------------------------------------------------------


def test_goa_december_party_peak_with_christmas_nye() -> None:
    """Peak season + high-impact festival → very_peak crowd, party-vibe modifiers."""
    signals = extract_signals(
        _trip(
            destination="Goa, India",
            date_from="2026-12-25",
            date_to="2027-01-01",
            vibes=["nightlife", "beaches"],
            budget="Medium",
        )
    )
    assert signals.region == "india"
    assert signals.season == "peak"
    assert signals.is_festival_window is True
    assert "Christmas/NYE" in signals.active_festivals
    assert signals.crowd_level == "very_peak"
    # Nightlife + beaches both up-weight YouTube
    assert signals.vibe_source_weights["youtube"] > signals.vibe_source_weights["blog"]
    # Very-peak crowds should suggest avoidance
    assert any("avoid crowds" in m or "hidden gems" in m for m in signals.query_modifiers)


def test_manali_july_monsoon_adventure_low_crowd() -> None:
    """Hill-station monsoon → low crowd, adventure vibes up-weight YouTube, weather warning."""
    signals = extract_signals(
        _trip(
            destination="Manali, India",
            date_from="2026-07-10",
            date_to="2026-07-17",
            vibes=["adventure", "nature"],
            budget="Low",
        )
    )
    assert signals.region == "india"
    assert signals.season == "monsoon"
    assert signals.crowd_level == "low"
    # Adventure dominates → youtube is the largest weight
    assert signals.vibe_source_weights["youtube"] >= signals.vibe_source_weights["reddit"]
    assert signals.vibe_source_weights["youtube"] > signals.vibe_source_weights["blog"]
    # Warnings should cover monsoon
    assert any("monsoon" in w.lower() or "landslide" in w.lower() for w in signals.warnings)


def test_paris_august_peak_summer_luxury_culture() -> None:
    """European peak summer + luxury/culture vibes → blog dominates."""
    signals = extract_signals(
        _trip(
            destination="Paris, France",
            date_from="2026-08-05",
            date_to="2026-08-12",
            vibes=["luxury", "culture"],
            budget="High",
        )
    )
    assert signals.region == "europe"
    assert signals.season == "peak"
    assert signals.crowd_level in ("peak", "very_peak")
    # Luxury + culture both blog-leaning → blog should be the largest weight
    assert signals.vibe_source_weights["blog"] > signals.vibe_source_weights["youtube"]
    assert signals.vibe_source_weights["blog"] > signals.vibe_source_weights["reddit"]
    assert any("best reviewed" in m or "cultural landmarks" in m for m in signals.query_modifiers)


def test_bali_june_off_season_relaxation() -> None:
    """SEA shoulder/monsoon, low crowd, relaxation vibe → blog slightly up."""
    signals = extract_signals(
        _trip(
            destination="Bali, Indonesia",
            date_from="2026-06-01",
            date_to="2026-06-08",
            vibes=["relaxation", "beaches"],
            budget="Medium",
        )
    )
    assert signals.region == "southeast_asia"
    assert signals.season == "monsoon"  # SEA Jun-Aug = rainy under our rules
    assert signals.crowd_level == "low"
    # Weights normalise to sum=1, so just sanity-check they're reasonable
    assert all(0.2 < w < 0.5 for w in signals.vibe_source_weights.values())


def test_nyc_may_shoulder_foodie_culture() -> None:
    """North America shoulder season, balanced foodie/culture vibe."""
    signals = extract_signals(
        _trip(
            destination="New York, USA",
            date_from="2026-05-10",
            date_to="2026-05-15",
            duration_days=5,
            vibes=["foodie", "culture"],
            budget="Medium",
        )
    )
    assert signals.region == "north_america"
    assert signals.season == "shoulder"
    assert signals.crowd_level == "moderate"
    # Foodie + culture mix — neither extreme
    weights = signals.vibe_source_weights
    assert all(0.25 < w < 0.45 for w in weights.values())
    assert any("local cuisine" in m or "cultural landmarks" in m for m in signals.query_modifiers)


# ---------------------------------------------------------------------------
# Integration tests — these are the real DoD.
# ---------------------------------------------------------------------------


def test_signals_differ_by_destination_same_dates_same_vibes() -> None:
    """Same dates + vibes, different destinations → fundamentally different signals.

    July 10–17 in India (Goa plains) is monsoon; in Paris it is peak summer.
    This is the core promise of the signals layer.
    """
    base = dict(
        date_from="2026-07-10",
        date_to="2026-07-17",
        duration_days=7,
        vibes=["adventure"],
        budget="Medium",
    )
    goa = extract_signals(_trip(destination="Goa, India", **base))
    paris = extract_signals(_trip(destination="Paris, France", **base))

    assert goa.region != paris.region
    assert goa.season != paris.season  # monsoon vs peak
    assert goa.crowd_level != paris.crowd_level  # low vs peak
    # Their query modifiers should not be identical
    assert set(goa.query_modifiers) != set(paris.query_modifiers)


def test_signals_differ_by_dates_same_destination() -> None:
    """Same destination + vibes, different dates → different signals.

    Goa in July is monsoon with no festival; Goa in late December is peak with
    Christmas/NYE festival. If the signals layer is doing its job, every field
    that depends on time should differ.
    """
    july = extract_signals(
        _trip(
            destination="Goa, India",
            date_from="2026-07-01",
            date_to="2026-07-08",
            vibes=["beaches"],
        )
    )
    december = extract_signals(
        _trip(
            destination="Goa, India",
            date_from="2026-12-25",
            date_to="2027-01-01",
            vibes=["beaches"],
        )
    )

    assert july.season == "monsoon"
    assert december.season == "peak"
    assert december.is_festival_window is True
    assert july.is_festival_window is False
    assert july.crowd_level != december.crowd_level


def test_vibe_weights_sum_to_one() -> None:
    """Weights are normalised proportions — synthesizer relies on this."""
    for vibes in [["luxury"], ["adventure", "nature"], ["foodie", "culture", "budget"], []]:
        signals = extract_signals(_trip(vibes=vibes))
        total = sum(signals.vibe_source_weights.values())
        assert math.isclose(total, 1.0, abs_tol=0.01), f"vibes={vibes} sum={total}"
