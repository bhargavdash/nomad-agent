"""OpenStreetMap Nominatim geocoder — free, no API key.

Used by the geo layer to turn city names into lat/lng. Nominatim's usage policy
requires a descriptive User-Agent and ≤ 1 request/second; we honour both and
cache results in-process (a destination's cities are geocoded once per process;
Milestone C will move this cache to Redis). Every failure path returns None so
the geo layer degrades gracefully to "no coordinates".
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app import cache as redis_cache

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim requires a real, identifying UA (generic httpx UA gets 403'd).
_USER_AGENT = "nomad-agent/0.1 (travel itinerary planner; contact: nomad_dev)"
_MIN_INTERVAL_SECONDS = 1.1  # stay just under the 1 req/s policy
_TIMEOUT_SECONDS = 10.0

# In-process caches. `_cache` keyed by normalised query string.
_cache: dict[str, tuple[float, float] | None] = {}
_rate_lock = asyncio.Lock()
_last_call_at: float = 0.0


async def geocode(query: str) -> tuple[float, float] | None:
    """Return (lat, lng) for a place query, or None on miss/error.

    Results (including misses, cached as None) are memoised per process so we
    never geocode the same place twice. Rate-limited to honour Nominatim policy.
    """
    key = " ".join(query.lower().split())
    if not key:
        return None
    if key in _cache:
        return _cache[key]

    # Cross-process L1: Redis (persists geocodes ~forever — geography is stable).
    persisted = await redis_cache.get_cached_geocode(query)
    if persisted is not None:
        _cache[key] = persisted
        return persisted

    global _last_call_at
    async with _rate_lock:
        # Another coroutine may have fetched this while we waited for the lock.
        if key in _cache:
            return _cache[key]
        # Space calls ≥ _MIN_INTERVAL_SECONDS apart.
        loop = asyncio.get_event_loop()
        wait = _MIN_INTERVAL_SECONDS - (loop.time() - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            result = await _fetch(query)
        except Exception as e:  # noqa: BLE001
            logger.warning("geocode.failed query=%r err=%s", query, e)
            result = None
        _last_call_at = loop.time()

    _cache[key] = result
    if result is not None:
        await redis_cache.set_cached_geocode(query, result)
    return result


async def _fetch(query: str) -> tuple[float, float] | None:
    params = {"q": query, "format": "json", "limit": 1}
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "en"}
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, headers=headers) as client:
        resp = await client.get(NOMINATIM_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list) or not data:
        logger.info("geocode.miss query=%r", query)
        return None
    top = data[0]
    try:
        return float(top["lat"]), float(top["lon"])
    except (KeyError, TypeError, ValueError):
        return None
