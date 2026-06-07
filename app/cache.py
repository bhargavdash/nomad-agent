"""Redis cache layer — destination research pool (L0) + geocodes.

Why: destination *research* is the expensive, reusable artefact. Caching the
merged discovery pool per destination lets repeat destinations skip all four
research agents (~13 LLM calls + ~50 API calls) and go straight to a fresh
personalised synthesis — the "research once, personalise many" model.

Posture: **caching is an optimisation, never a hard dependency.**
If `REDIS_URL` is unset or Redis is unreachable, every function degrades to a
no-op / miss and the pipeline runs exactly as it did before. All Redis calls
are wrapped so a cache outage can never fail a trip.

Keys (all prefixed with the configurable CACHE_VERSION so a bump invalidates
everything at once):
  nomad:research:{ver}:{slug}:{season}  → JSON list of ResearchDiscovery (TTL: ~45d)
  nomad:geo:{ver}:{slug}                → "lat,lng" geocode              (TTL: ~1y)

L0 design: the research key is destination × season only — vibe_cluster is
intentionally NOT part of the key. Research agents run in broad mode and
collect a vibe-neutral pool covering all four clusters. Per-user vibe
preference is applied at read time by app/pool_filter.py before synthesis.

This means one cache entry per destination+season is shared by every user
regardless of their vibes, and re-iterated trips with updated vibes skip
research entirely and re-run only the cheap filter + synthesizer steps.

Known limitation: a small number of date-dependent query slots (active
festival queries in the Reddit agent, crowd-level branch in Reddit Q3) are
still shaped by the first cold-filling user's travel dates. These are
destination+time signals (not user preference) and their contamination risk
is low — festivals are rare and crowd-level correlates with season.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings
from app.schemas import ResearchDiscovery

logger = logging.getLogger(__name__)

_GEOCODE_TTL_SECONDS = 365 * 24 * 3600  # geography doesn't move

# Lazily-created shared async client. `False` = "tried and unavailable" so we
# don't retry connecting on every call; None = "not yet initialised".
_client: Any | None = None
_disabled = False


def _slug(text: str) -> str:
    """Stable cache slug: lowercased, alphanumerics joined by single hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")


def _get_client() -> Any | None:
    """Return a shared redis.asyncio client, or None if caching is disabled."""
    global _client, _disabled
    if _disabled:
        return None
    if _client is not None:
        return _client
    if not settings.REDIS_URL:
        _disabled = True
        logger.info("cache.disabled: REDIS_URL not set — running without cache")
        return None
    try:
        from redis.asyncio import from_url

        _client = from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        return _client
    except Exception as e:  # noqa: BLE001
        logger.warning("cache.connect_failed: %s — running without cache", e)
        _disabled = True
        return None


def _research_key(destination: str, season: str) -> str:
    return f"nomad:research:{settings.CACHE_VERSION}:{_slug(destination)}:{_slug(season)}"


def _geo_key(query: str) -> str:
    return f"nomad:geo:{settings.CACHE_VERSION}:{_slug(query)}"


# ---------------------------------------------------------------------------
# L1 — destination research pool
# ---------------------------------------------------------------------------


async def get_cached_research(
    destination: str, season: str
) -> list[ResearchDiscovery] | None:
    """Return the cached L0 discovery pool for a destination+season, or None on miss.

    The pool is vibe-agnostic — all four vibe clusters are represented. The
    caller should apply pool_filter.filter_pool_for_user() before passing
    discoveries to the synthesizer.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(_research_key(destination, season))
        if not raw:
            return None
        data = json.loads(raw)
        pool = [ResearchDiscovery.model_validate(d) for d in data]
        logger.info("cache.research HIT dest=%r season=%r n=%d", destination, season, len(pool))
        return pool
    except Exception as e:  # noqa: BLE001
        logger.warning("cache.research get failed dest=%r: %s", destination, e)
        return None


async def set_cached_research(
    destination: str,
    season: str,
    discoveries: list[ResearchDiscovery],
) -> None:
    """Cache the L0 discovery pool keyed by destination+season (best-effort).

    Stores the full broad pool. Per-user filtering is applied at read time.
    """
    client = _get_client()
    if client is None or not discoveries:
        return
    try:
        payload = json.dumps([d.model_dump() for d in discoveries])
        ttl = settings.RESEARCH_CACHE_TTL_DAYS * 24 * 3600
        await client.set(_research_key(destination, season), payload, ex=ttl)
        logger.info(
            "cache.research SET dest=%r season=%r n=%d ttl_days=%d",
            destination,
            season,
            len(discoveries),
            settings.RESEARCH_CACHE_TTL_DAYS,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("cache.research set failed dest=%r: %s", destination, e)


# ---------------------------------------------------------------------------
# Geocode cache (shared by the geo layer)
# ---------------------------------------------------------------------------


async def get_cached_geocode(query: str) -> tuple[float, float] | None:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(_geo_key(query))
        if not raw:
            return None
        lat_s, lng_s = raw.split(",", 1)
        return float(lat_s), float(lng_s)
    except Exception as e:  # noqa: BLE001
        logger.warning("cache.geo get failed query=%r: %s", query, e)
        return None


async def set_cached_geocode(query: str, latlng: tuple[float, float]) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        await client.set(_geo_key(query), f"{latlng[0]},{latlng[1]}", ex=_GEOCODE_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001
        logger.warning("cache.geo set failed query=%r: %s", query, e)
