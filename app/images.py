"""Resolve + self-host itinerary/trending imagery at build time.

For the trip hero (destination) and each unique city — and for each trending
destination — resolve the best real photo (app/tools/place_image), download the
bytes, and upload them to the public Supabase Storage bucket so the app serves
images it controls.

Storage is keyed by the PLACE, not the trip: the same (place, context) maps to
one stored object reused across every trip, so 1000 trips to Jaipur store ONE
Jaipur photo. A cheap existence check skips resolution + download + upload
entirely when a place is already hosted — the bucket doubles as a persistent,
cross-trip cache. Uploads also carry a 1-year cache-control so the Supabase CDN
and browsers cache the bytes.

Falls back to the upstream URL when hosting is unavailable, and to None when no
photo is found (the frontend then shows its deterministic themed fallback).
Best-effort throughout — never raises.

This module is the agent's image-resolution entrypoint. The agent is the SOLE
writer of imagery; Node and web only read the resulting URLs (no read/write race).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import httpx

from app.config import settings
from app.db import supabase_writer
from app.schemas import AIItinerary, TrendingPayload
from app.tools.place_image import resolve_place_image_url

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_MAX_BYTES = 8 * 1024 * 1024  # safety cap; 1280px thumbnails are far smaller


def _place_key(query: str, context: str) -> str:
    """Stable content-address for a (place, context) pair. The same place across
    different trips -> same key -> one stored object (no duplicate storage).
    Normalised (strip + lower) so "Jaipur" and " jaipur " dedupe together.
    """
    raw = f"{query.strip().lower()}|{context.strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _public_url(path: str) -> str:
    base = settings.SUPABASE_URL.rstrip("/")
    return f"{base}/storage/v1/object/public/{settings.SUPABASE_IMAGE_BUCKET}/{path}"


async def _object_exists(client: httpx.AsyncClient, url: str) -> bool:
    """Cheap HEAD against the public object URL: 200 -> already hosted."""
    try:
        resp = await client.head(url)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def _download(client: httpx.AsyncClient, url: str) -> tuple[bytes, str] | None:
    try:
        resp = await client.get(url, headers={"User-Agent": "NomadAgent/1.0"})
    except Exception as e:  # noqa: BLE001
        logger.warning("images.download_failed url=%r err=%s", url, e)
        return None
    if resp.status_code != 200:
        return None
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        return None
    data = resp.content
    if not data or len(data) > _MAX_BYTES:
        return None
    return data, ctype


async def _cached_or_host(client: httpx.AsyncClient, query: str, context: str) -> str | None:
    """Self-hosted public URL for a place, deduped + cached by place key.

    Cache hit (object already in the bucket) -> return its URL with no
    resolve/download/upload. Miss -> resolve -> download -> upload once. Falls
    back to the upstream URL if hosting is unavailable, None if nothing resolves.
    """
    have_storage = bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)
    path = f"places/{_place_key(query, context)}"

    if have_storage:
        url = _public_url(path)
        if await _object_exists(client, url):
            return url  # cache hit — another trip already hosted this place

    upstream = await resolve_place_image_url(query, context)
    if not upstream:
        return None
    downloaded = await _download(client, upstream)
    if downloaded is None:
        return upstream  # couldn't fetch bytes — still better than a fallback
    data, ctype = downloaded
    hosted = await supabase_writer.upload_public_image(data, ctype, path)
    # On success return the canonical public URL (identical to the cache-hit
    # URL, so the DB stores one stable URL per place); upstream if storage off.
    return _public_url(path) if hosted else upstream


async def resolve_and_store_itinerary_images(
    trip_id: str, destination: str, itinerary: AIItinerary
) -> tuple[str | None, dict[str, str | None]]:
    """Hero (destination) + one image per unique city, deduped so a repeated
    city resolves once. Never raises — returns whatever was found."""
    cities = list(dict.fromkeys(d.city for d in itinerary.days if d.city))

    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        results = await asyncio.gather(
            _cached_or_host(client, destination, ""),
            *(_cached_or_host(client, city, destination) for city in cities),
        )

    hero_url: str | None = results[0]
    city_images: dict[str, str | None] = dict(zip(cities, results[1:]))
    logger.info(
        "images.resolved trip=%s hero=%s cities=%d/%d",
        trip_id,
        bool(hero_url),
        sum(1 for u in city_images.values() if u),
        len(cities),
    )
    return hero_url, city_images


async def resolve_and_store_trending_images(payload: TrendingPayload) -> None:
    """Set a self-hosted ``imageUrl`` on every trending destination, in place.

    Same place-keyed dedup/cache as trips. Always overwrites imageUrl (None when
    nothing resolves) so a stale/LLM-hallucinated value can't leak. Never raises.
    """
    dests = [*payload.india, *payload.international]

    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        urls = await asyncio.gather(*(_cached_or_host(client, d.name, d.country) for d in dests))

    resolved = sum(1 for url in urls if url)
    for dest, url in zip(dests, urls):
        dest.imageUrl = url
    logger.info("images.trending_resolved %d/%d", resolved, len(dests))
