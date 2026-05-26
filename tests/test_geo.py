"""Unit tests for the geo layer (Milestone D). No network, no LLM — mocked."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.geo import GeoBrief, GeoLeg, build_geo_brief
from app.geo.distance import drive_time_hint, haversine_km, road_km
from app.geo.planner import _nearest_neighbour_order, _offset_hours, _tz_name
from app.geo.sun import sun_times
from app.schemas import TripParams
from app.signals import extract_signals

# Real-ish coordinates for the Rajasthan circuit.
JAIPUR = (26.9124, 75.7873)
JODHPUR = (26.2389, 73.0243)
JAISALMER = (26.9157, 70.9083)
UDAIPUR = (24.5854, 73.7125)


def _trip(**ov) -> TripParams:
    base = dict(
        trip_id="t1", user_id="u1", destination="Rajasthan, India",
        date_from="2026-12-20", date_to="2026-12-31", duration_days=11,
        travelers="2", vibes=["heritage"], accommodation="Heritage Haveli",
        pace="Balanced", budget="Medium", preferences=None,
    )
    base.update(ov)
    return TripParams(**base)


# --- distance ---------------------------------------------------------------


def test_haversine_and_road_km() -> None:
    crow = haversine_km(*JODHPUR, *JAISALMER)
    assert 220 < crow < 240  # ~225 km crow-flies
    rd = road_km(*JODHPUR, *JAISALMER)
    assert 280 < rd < 310  # ~285 km by road (actual)


def test_drive_time_hint_formats() -> None:
    assert drive_time_hint(25) == "~30m"
    assert drive_time_hint(50) == "~1h"
    assert drive_time_hint(285).startswith("~5h")


# --- sun --------------------------------------------------------------------


def test_sun_times_jaipur_december_matches_reality() -> None:
    # Jaipur, Dec 25, IST(+5.5): sunrise ~7:14, sunset ~17:48 (real almanac).
    sr, ss = sun_times(date(2026, 12, 25), *JAIPUR, 5.5)
    sr_h, sr_m = map(int, sr.split(":"))
    ss_h, ss_m = map(int, ss.split(":"))
    assert sr_h == 7 and 5 <= sr_m <= 25      # ~7:1x
    assert ss_h == 17 and 30 <= ss_m <= 55    # ~17:4x


def test_sun_times_polar_returns_none() -> None:
    # Far-north latitude in deep winter → polar night → None.
    assert sun_times(date(2026, 12, 21), 80.0, 20.0, 1.0) is None


# --- tz + ordering ----------------------------------------------------------


def test_tz_name_mapping() -> None:
    assert _tz_name("india", "rajasthan, india") == "Asia/Kolkata"
    assert _tz_name("europe", "paris, france") == "Europe/Paris"
    assert _tz_name("southeast_asia", "bangkok, thailand") == "Asia/Bangkok"
    assert _tz_name("unknown", "atlantis") is None  # → sun times skipped


def test_offset_hours_is_dst_aware() -> None:
    # Paris: CET (+1) in winter, CEST (+2) in summer — the DST fix.
    assert _offset_hours("Europe/Paris", date(2026, 1, 15)) == 1.0
    assert _offset_hours("Europe/Paris", date(2026, 6, 15)) == 2.0
    # India: always +5:30, no DST.
    assert _offset_hours("Asia/Kolkata", date(2026, 6, 15)) == 5.5


def test_sun_times_paris_june_dst_correct() -> None:
    # Paris ~48.85N on Jun 15 sunrise ≈ 05:47 CEST (was ~04:48 with the old
    # fixed +1 offset). Verifies the DST fix end-to-end via the +2 offset.
    sr, _ss = sun_times(date(2026, 6, 15), 48.8566, 2.3522, 2.0)
    h, m = map(int, sr.split(":"))
    assert h == 5 and 35 <= m <= 59


def test_nearest_neighbour_fixes_backtracking_order() -> None:
    coords = {
        "Jaipur": JAIPUR, "Jodhpur": JODHPUR,
        "Jaisalmer": JAISALMER, "Udaipur": UDAIPUR,
    }
    # A deliberately bad order that bounces east-west: Jaisalmer (far W) then
    # back to Jaipur (far E) then Udaipur (S). NN from the start should reorder.
    bad = ["Jaisalmer", "Jaipur", "Udaipur", "Jodhpur"]
    order, improved = _nearest_neighbour_order(bad, coords)
    assert order[0] == "Jaisalmer"  # NN keeps the entry city
    assert improved is True


# --- GeoBrief rendering -----------------------------------------------------


def test_geobrief_empty_renders_blank() -> None:
    assert GeoBrief().is_empty()
    assert GeoBrief().to_prompt_block() == ""


def test_geobrief_prompt_block_contains_facts() -> None:
    brief = GeoBrief(
        ordered_cities=["Jaipur", "Jodhpur"],
        legs=[GeoLeg("Jaipur", "Jodhpur", 337, "~6h45m")],
        sun={"Jaipur": ("7:13", "17:40")},
    )
    block = brief.to_prompt_block()
    assert "Jaipur → Jodhpur" in block
    assert "337 km" in block and "~6h45m" in block
    assert "sunrise 7:13" in block


# --- build_geo_brief (mocked pick + geocode) --------------------------------


@pytest.mark.asyncio
async def test_build_geo_brief_happy_path() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    coords = {
        "jaipur, rajasthan, india": JAIPUR,
        "jodhpur, rajasthan, india": JODHPUR,
        "jaisalmer, rajasthan, india": JAISALMER,
        "udaipur, rajasthan, india": UDAIPUR,
    }

    async def fake_geocode(q: str):
        return coords.get(" ".join(q.lower().split()))

    with (
        patch(
            "app.geo.planner._pick_cities",
            AsyncMock(return_value=["Jaipur", "Jodhpur", "Jaisalmer", "Udaipur"]),
        ),
        patch("app.geo.planner.geocode", side_effect=fake_geocode),
    ):
        brief = await build_geo_brief(trip, signals)

    assert not brief.is_empty()
    assert set(brief.ordered_cities) == {"Jaipur", "Jodhpur", "Jaisalmer", "Udaipur"}
    assert len(brief.legs) == 3
    # India → sun times present for each city.
    assert len(brief.sun) == 4
    assert all(":" in sr and ":" in ss for sr, ss in brief.sun.values())


@pytest.mark.asyncio
async def test_build_geo_brief_empty_when_pick_fails() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    with patch("app.geo.planner._pick_cities", AsyncMock(return_value=[])):
        brief = await build_geo_brief(trip, signals)
    assert brief.is_empty()


@pytest.mark.asyncio
async def test_build_geo_brief_empty_when_geocode_fails() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    with (
        patch("app.geo.planner._pick_cities", AsyncMock(return_value=["Jaipur"])),
        patch("app.geo.planner.geocode", AsyncMock(return_value=None)),
    ):
        brief = await build_geo_brief(trip, signals)
    assert brief.is_empty()
