"""Resolve + self-host itinerary imagery at build time.

For the trip hero (destination) and each unique city, resolve the best real
photo (app/tools/place_image), download the bytes, and upload them to the
public Supabase Storage bucket so the app serves images it controls — no
hotlink drift, no upstream throttling. Falls back to the upstream URL when an
upload isn't possible, and to None when no photo is found (the frontend then
shows its deterministic themed fallback). Best-effort throughout — never raises.

This module is the agent's image-resolution entrypoint. The agent is the SOLE
writer of trip imagery; Node and web only read the resulting URLs, so there is
no read/write race.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from app.db import supabase_writer
from app.schemas import AIItinerary, TrendingPayload
from app.tools.place_image import resolve_place_image_url

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_MAX_BYTES = 8 * 1024 * 1024  # safety cap; 1280px thumbnails are far smaller
_EXT_BY_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/avif": "avif",
}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "place"


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


async def _resolve_and_host(
    client: httpx.AsyncClient, query: str, context: str, path_stem: str
) -> str | None:
    """Resolve -> download -> upload. Returns a self-hosted public URL; falls
    back to the upstream URL if the bytes can't be fetched/uploaded; None if
    nothing resolved."""
    upstream = await resolve_place_image_url(query, context)
    if not upstream:
        return None
    downloaded = await _download(client, upstream)
    if downloaded is None:
        return upstream  # couldn't fetch bytes — still better than a fallback
    data, ctype = downloaded
    ext = _EXT_BY_TYPE.get(ctype, "jpg")
    hosted = await supabase_writer.upload_public_image(data, ctype, f"{path_stem}.{ext}")
    return hosted or upstream  # upload unavailable — serve the upstream URL


async def resolve_and_store_itinerary_images(
    trip_id: str, destination: str, itinerary: AIItinerary
) -> tuple[str | None, dict[str, str | None]]:
    """Hero (destination) + one image per unique city, deduped so a repeated
    city resolves once. Never raises — returns whatever was found."""
    cities = list(dict.fromkeys(d.city for d in itinerary.days if d.city))

    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        results = await asyncio.gather(
            _resolve_and_host(client, destination, "", f"trips/{trip_id}/hero"),
            *(
                _resolve_and_host(
                    client, city, destination, f"trips/{trip_id}/city-{_slug(city)}"
                )
                for city in cities
            ),
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

    Called at trending-refresh write time so the Node /trending endpoint serves
    stored URLs directly — no lazy on-read hydration, hence no race. Always
    overwrites imageUrl (with None when nothing resolves) so a stale or
    LLM-hallucinated value can't leak through. Never raises.
    """
    dests = [*payload.india, *payload.international]

    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        urls = await asyncio.gather(
            *(
                _resolve_and_host(
                    client, d.name, d.country, f"trending/{_slug(d.name)}-{_slug(d.country)}"
                )
                for d in dests
            )
        )

    resolved = sum(1 for url in urls if url)
    for dest, url in zip(dests, urls):
        dest.imageUrl = url
    logger.info("images.trending_resolved %d/%d", resolved, len(dests))
