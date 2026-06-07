"""Tests for the Redis cache layer (L0) + pipeline cache behavior.

Uses fakeredis (no real Redis) and verifies graceful degradation when caching
is disabled.

L0 design: cache key is destination+season only (vibe-agnostic). The old
FI-6 vibe_cluster dimension has been removed from the key — the broad pool
is now shared across all vibe preferences for a given destination+season and
narrowed at read time by pool_filter.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

import app.cache as cache_mod
from app.schemas import ResearchDiscovery, TripParams
from app.signals import extract_signals


def _disc(title: str, source: str = "blog") -> ResearchDiscovery:
    return ResearchDiscovery(
        id=f"id-{title}",
        title=title,
        body=f"About {title}.",
        tags=["t"],
        source=source,  # type: ignore[arg-type]
    )


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache_mod, "_get_client", lambda: client)
    return client


# --- slug + keys ------------------------------------------------------------


def test_slug_normalises() -> None:
    assert cache_mod._slug("Goa, India") == "goa-india"
    assert cache_mod._slug("  Rajasthan,  India ") == "rajasthan-india"
    assert cache_mod._slug("New York, USA") == "new-york-usa"


# --- research round-trip ----------------------------------------------------


@pytest.mark.asyncio
async def test_research_roundtrip(fake_redis) -> None:
    dest = "Goa, India"
    season = "peak"
    assert await cache_mod.get_cached_research(dest, season) is None  # cold miss
    pool = [_disc("Anjuna Beach", "youtube"), _disc("Fontainhas", "blog")]
    await cache_mod.set_cached_research(dest, season, pool)
    got = await cache_mod.get_cached_research(dest, season)
    assert got is not None
    assert [d.title for d in got] == ["Anjuna Beach", "Fontainhas"]
    assert got[0].source == "youtube"


@pytest.mark.asyncio
async def test_research_key_is_destination_season(fake_redis) -> None:
    # Same destination+season, different casing/spacing → same cache entry.
    await cache_mod.set_cached_research("Goa, India", "peak", [_disc("X")])
    assert await cache_mod.get_cached_research("  goa,  india ", "peak") is not None


@pytest.mark.asyncio
async def test_research_different_vibes_share_same_pool(fake_redis) -> None:
    # L0 design: vibe_cluster is NOT a cache dimension.
    # Both adventure and foodie users for Goa+peak read the same pool.
    pool = [_disc("Anjuna Beach"), _disc("Vinayak Fish Curry")]
    await cache_mod.set_cached_research("Goa", "peak", pool)

    # Any vibe combination reading Goa+peak gets the same pool.
    got_adventure = await cache_mod.get_cached_research("Goa", "peak")
    got_foodie = await cache_mod.get_cached_research("Goa", "peak")
    assert got_adventure is not None and len(got_adventure) == 2
    assert got_foodie is not None and len(got_foodie) == 2
    assert got_adventure[0].title == got_foodie[0].title


@pytest.mark.asyncio
async def test_research_different_seasons_are_separate(fake_redis) -> None:
    # Season is still a cache dimension — monsoon Goa ≠ peak Goa.
    await cache_mod.set_cached_research("Goa", "peak", [_disc("Dec Stop")])
    assert await cache_mod.get_cached_research("Goa", "monsoon") is None


@pytest.mark.asyncio
async def test_geocode_roundtrip(fake_redis) -> None:
    assert await cache_mod.get_cached_geocode("Jaipur") is None
    await cache_mod.set_cached_geocode("Jaipur", (26.9124, 75.7873))
    assert await cache_mod.get_cached_geocode("Jaipur") == (26.9124, 75.7873)


# --- graceful degradation ---------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_when_no_client(monkeypatch) -> None:
    monkeypatch.setattr(cache_mod, "_get_client", lambda: None)
    assert await cache_mod.get_cached_research("Goa", "peak") is None
    # set is a no-op (must not raise) when caching is disabled.
    await cache_mod.set_cached_research("Goa", "peak", [_disc("X")])
    assert await cache_mod.get_cached_geocode("Goa") is None
    await cache_mod.set_cached_geocode("Goa", (1.0, 2.0))


@pytest.mark.asyncio
async def test_corrupt_payload_returns_none(fake_redis) -> None:
    await fake_redis.set(cache_mod._research_key("Goa", "peak"), "not json{")
    assert await cache_mod.get_cached_research("Goa", "peak") is None  # graceful


# --- pipeline cache behavior ------------------------------------------------


def _trip() -> TripParams:
    return TripParams(
        trip_id="t1",
        user_id="u1",
        destination="Goa, India",
        date_from="2026-12-15",
        date_to="2026-12-20",
        duration_days=5,
        travelers="2",
        vibes=["beaches"],
        accommodation="Hotel",
        pace="Balanced",
        budget="Medium",
        preferences=None,
    )


@pytest.mark.asyncio
async def test_research_gate_hit_skips_agents(monkeypatch) -> None:
    from app.graph import pipeline

    pool = [_disc("Anjuna Beach", "youtube")]
    monkeypatch.setattr(pipeline.cache, "get_cached_research", AsyncMock(return_value=pool))
    trip = _trip()
    state = {"trip_params": trip, "signals": extract_signals(trip)}

    gate = await pipeline.research_gate_node(state)
    assert gate["research_cache"] == pool

    # With research_cache set, each research node no-ops (no API/LLM).
    hit_state = {**state, "research_cache": pool}
    assert await pipeline.youtube_node(hit_state) == {"yt_discoveries": []}
    assert await pipeline.reddit_node(hit_state) == {"reddit_discoveries": []}
    assert await pipeline.google_node(hit_state) == {"google_discoveries": []}


@pytest.mark.asyncio
async def test_merge_uses_cached_pool_on_hit(monkeypatch) -> None:
    from app.graph import pipeline

    pool = [_disc("Anjuna Beach", "youtube"), _disc("Baga", "reddit")]
    monkeypatch.setattr(pipeline.supabase_writer, "write_discoveries", AsyncMock(return_value=None))
    trip = _trip()
    out = await pipeline.merge_node(
        {"trip_params": trip, "signals": extract_signals(trip), "research_cache": pool}
    )
    assert out["all_discoveries"] == pool


@pytest.mark.asyncio
async def test_pool_filter_narrows_large_pool(monkeypatch) -> None:
    from app.graph import pipeline

    # Build a pool larger than DEFAULT_MAX_ITEMS (15).
    big_pool = [_disc(f"Place {i}", "blog") for i in range(20)]
    trip = _trip()
    signals = extract_signals(trip)
    state = {"trip_params": trip, "signals": signals, "all_discoveries": big_pool}

    out = await pipeline.pool_filter_node(state)
    assert "synthesizer_pool" in out
    assert len(out["synthesizer_pool"]) <= 15


@pytest.mark.asyncio
async def test_pool_filter_passthrough_small_pool(monkeypatch) -> None:
    from app.graph import pipeline

    small_pool = [_disc("Anjuna Beach", "youtube"), _disc("Fontainhas", "blog")]
    trip = _trip()
    signals = extract_signals(trip)
    state = {"trip_params": trip, "signals": signals, "all_discoveries": small_pool}

    out = await pipeline.pool_filter_node(state)
    assert out["synthesizer_pool"] == small_pool
