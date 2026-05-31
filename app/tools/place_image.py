"""Place-image resolver — best real photo for a named place.

Resolves a real photo of a destination / city / landmark, returning a SCALED
thumbnail URL or None. Primary source is Wikipedia (excellent for named places);
Pexels is an optional long-tail fallback for places without a Wikipedia page.
The caller self-hosts the bytes (see app/images.py) and the frontend falls back
to deterministic themed imagery when this returns None.

Ported + extended from nomad-api's placeImage.service.ts. Hard-won rules kept:
  - Request pithumbsize=1280 and use thumbnail.source — NEVER the original
    (originals are routinely 10-20 MB; 1280 forces a scaled /thumb/ URL ~100-450 KB).
  - Exact page title first (follows redirects), then fuzzy search — fuzzy alone
    grabs the wrong page (e.g. "Munnar" -> parent "Idukki district").
  - Reject non-photo page-images: maps, flags, coats of arms, logos, icons, SVGs.
  - Memoised in-process (long-lived server) so repeat lookups are free.
"""

from __future__ import annotations

import logging
import re
from typing import TypeGuard
from urllib.parse import unquote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_WIKI_ENDPOINT = "https://en.wikipedia.org/w/api.php"
_PEXELS_ENDPOINT = "https://api.pexels.com/v1/search"
_USER_AGENT = "NomadAgent/1.0 (https://nomad.app; travel itinerary place images)"
_THUMB_SIZE = 1280
# Bounded per-call budget: a slow Wikipedia/Pexels call must never stall the
# build phase. connect=3s, total=6s — well under the pipeline's own budget.
_TIMEOUT = httpx.Timeout(6.0, connect=3.0)

_NON_PHOTO = re.compile(
    r"\.svg|flag|coat[_-]?of[_-]?arms|locator|location|emblem|\bseal\b|\blogo\b|\bicon\b|disambig",
    re.IGNORECASE,
)
_MAP_PREFIX = re.compile(r"^map[-_]", re.IGNORECASE)
_MAP_INFIX = re.compile(r"[-_]map[-_.]", re.IGNORECASE)

# In-process memo (the FastAPI server is long-lived, so a city repeated across
# days — or across trips — resolves once). Bounded; oldest insertion evicted.
_MEMO: dict[str, str | None] = {}
_MEMO_MAX = 2000


def _remember(key: str, value: str | None) -> str | None:
    if len(_MEMO) >= _MEMO_MAX:
        oldest = next(iter(_MEMO))
        _MEMO.pop(oldest, None)
    _MEMO[key] = value
    return value


def _file_name(url: str) -> str:
    try:
        return unquote(url.split("?")[0].split("/")[-1])
    except Exception:  # noqa: BLE001
        return url


def _is_photo(url: str | None) -> TypeGuard[str]:
    if not url:
        return False
    name = _file_name(url)
    if _MAP_PREFIX.search(name) or _MAP_INFIX.search(name):
        return False
    return _NON_PHOTO.search(name) is None


def _clean_url(url: str) -> str:
    return url.split("?")[0]


def _pick_photo_url(pages: list[dict]) -> str | None:
    ordered = sorted(pages, key=lambda p: p.get("index", 99))
    for page in ordered:
        thumb = (page.get("thumbnail") or {}).get("source")
        if _is_photo(thumb):
            return _clean_url(thumb)
    return None


async def _wiki_pages(client: httpx.AsyncClient, params: dict[str, str]) -> list[dict] | None:
    query = {
        "action": "query",
        "format": "json",
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": str(_THUMB_SIZE),
        "origin": "*",
        **params,
    }
    try:
        resp = await client.get(
            _WIKI_ENDPOINT,
            params=query,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("place_image.wiki_failed params=%r err=%s", params, e)
        return None
    pages = (((data or {}).get("query") or {}).get("pages")) or {}
    return list(pages.values()) if pages else None


async def _from_wikipedia(client: httpx.AsyncClient, query: str, context: str) -> str | None:
    # 1. Exact title (follows redirects, e.g. "Amber Fort" -> "Amer Fort").
    exact = await _wiki_pages(client, {"titles": query, "redirects": "1"})
    if exact:
        hit = _pick_photo_url(exact)
        if hit:
            return hit
    # 2. Fuzzy search, biased with context (the trip destination / country).
    search = f"{query} {context}".strip() if context else query
    fuzzy = await _wiki_pages(
        client, {"generator": "search", "gsrsearch": search, "gsrlimit": "3"}
    )
    if fuzzy:
        hit = _pick_photo_url(fuzzy)
        if hit:
            return hit
    return None


async def _from_pexels(client: httpx.AsyncClient, query: str) -> str | None:
    """Long-tail fallback for places Wikipedia has no usable photo for.

    Gated on PEXELS_API_KEY — returns None (so the caller falls through to the
    frontend's deterministic themed fallback) when the key is unset.
    """
    if not settings.PEXELS_API_KEY:
        return None
    try:
        resp = await client.get(
            _PEXELS_ENDPOINT,
            params={"query": query, "per_page": "1", "orientation": "landscape"},
            headers={"Authorization": settings.PEXELS_API_KEY},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("place_image.pexels_failed q=%r err=%s", query, e)
        return None
    photos = (data or {}).get("photos") or []
    if not photos:
        return None
    src = (photos[0] or {}).get("src") or {}
    # `landscape` is a pre-cropped ~1200x627 variant — ideal for hero/banners.
    return src.get("landscape") or src.get("large") or src.get("original")


async def resolve_place_image_url(query: str, context: str = "") -> str | None:
    """Best real-photo URL for a place, or None. Never raises.

    `context` (the trip destination / country) disambiguates a city lookup,
    e.g. resolve_place_image_url("Udaipur", "Rajasthan, India").
    """
    q = query.strip()
    if not q:
        return None

    key = f"{q.lower()}|{context.strip().lower()}"
    if key in _MEMO:
        return _MEMO[key]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            url = await _from_wikipedia(client, q, context.strip())
            if not url:
                url = await _from_pexels(client, f"{q} {context}".strip())
        return _remember(key, url)
    except Exception as e:  # noqa: BLE001
        # Transient failure — don't memoise, so a later run can retry.
        logger.warning("place_image.resolve_failed q=%r err=%s", q, e)
        return None
