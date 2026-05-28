"""Tests for the Redis cache layer (Milestone C, L1) + pipeline cache behavior.

Uses fakeredis (no real Redis) and verifies graceful degradation when caching
is disabled. Pipeline tests patch the cache so research nodes no-op on a hit.
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
    assert await cache_mod.get_cached_research(dest) is None  # cold miss
    pool = [_disc("Anjuna Beach", "youtube"), _disc("Fontainhas", "blog")]
    await cache_mod.set_cached_research(dest, pool)
    got = await cache_mod.get_cached_research(dest)
    assert got is not None
    assert [d.title for d in got] == ["Anjuna Beach", "Fontainhas"]
    assert got[0].source == "youtube"


@pytest.mark.asyncio
async def test_research_key_is_destination_only(fake_redis) -> None:
    # Same destination, different casing/spacing → same cache entry.
    await cache_mod.set_cached_research("Goa, India", [_disc("X")])
    assert await cache_mod.get_cached_research("  goa,  india ") is not None


@pytest.mark.asyncio
async def test_geocode_roundtrip(fake_redis) -> None:
    assert await cache_mod.get_cached_geocode("Jaipur") is None
    await cache_mod.set_cached_geocode("Jaipur", (26.9124, 75.7873))
    assert await cache_mod.get_cached_geocode("Jaipur") == (26.9124, 75.7873)


# --- graceful degradation ---------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_when_no_client(monkeypatch) -> None:
    monkeypatch.setattr(cache_mod, "_get_client", lambda: None)
    assert await cache_mod.get_cached_research("Goa") is None
    # set is a no-op (must not raise) when caching is disabled.
    await cache_mod.set_cached_research("Goa", [_disc("X")])
    assert await cache_mod.get_cached_geocode("Goa") is None
    await cache_mod.set_cached_geocode("Goa", (1.0, 2.0))


@pytest.mark.asyncio
async def test_corrupt_payload_returns_none(fake_redis) -> None:
    await fake_redis.set(cache_mod._research_key("Goa"), "not json{")
    assert await cache_mod.get_cached_research("Goa") is None  # graceful, no raise


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
