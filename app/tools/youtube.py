"""YouTube Data API v3 tool — fetch authentic <60s Shorts for travel research.

Why Shorts and not long-form vlogs?
  Long-form travel YouTube is over-produced and sponsored. Shorts (<60s)
  tend to capture authentic "I just walked into this place" moments.
  Cross-Short aggregation (same place mentioned across 3+ creators) is
  a strong quality signal.

Why not just trust the search API's videoDuration=short filter?
  The "short" filter actually means <4 minutes, NOT the YouTube Shorts
  format. We must fetch contentDetails.duration and re-filter to <60s
  ourselves. That's the only reliable way to isolate Shorts.

Quota:
  search.list = 100 units, videos.list = 1 unit per call.
  Free tier = 10,000 units/day. One agent run ≈ 101 units (1 search + 25
  video lookups batched into 1 videos.list call). Plenty of headroom.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
SHORTS_MAX_DURATION_SECONDS = 60
DEFAULT_SEARCH_MAX_RESULTS = 25


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class YouTubeShort:
    video_id: str
    title: str
    channel_title: str
    description: str
    duration_seconds: int
    view_count: int
    published_at: str  # ISO 8601
    tags: list[str]

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/shorts/{self.video_id}"


# ---------------------------------------------------------------------------
# ISO 8601 duration parser (YouTube returns durations like "PT45S", "PT1M30S")
# ---------------------------------------------------------------------------

_ISO8601_RE = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)


def parse_iso8601_duration(duration: str) -> int:
    """Parse a YouTube ISO 8601 duration string to seconds. Returns 0 on parse failure."""
    if not duration:
        return 0
    match = _ISO8601_RE.match(duration)
    if not match:
        return 0
    h = int(match.group("hours") or 0)
    m = int(match.group("minutes") or 0)
    s = int(match.group("seconds") or 0)
    return h * 3600 + m * 60 + s


# ---------------------------------------------------------------------------
# YouTube Data API v3 calls
# ---------------------------------------------------------------------------


async def _search_short_videos(
    query: str, max_results: int, api_key: str
) -> list[str]:
    """Call search.list with videoDuration=short. Returns a list of videoIds."""
    params = {
        "part": "id",
        "q": query,
        "type": "video",
        "videoDuration": "short",  # <4 min — we re-filter to <60s below
        "maxResults": min(max_results, 50),
        "order": "relevance",
        "safeSearch": "moderate",
        "key": api_key,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{YOUTUBE_API_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()

    return [
        item["id"]["videoId"]
        for item in data.get("items", [])
        if item.get("id", {}).get("videoId")
    ]


async def _fetch_video_details(
    video_ids: list[str], api_key: str
) -> list[dict[str, Any]]:
    """Call videos.list (batched up to 50 IDs) → contentDetails + snippet + statistics."""
    if not video_ids:
        return []

    params = {
        "part": "contentDetails,snippet,statistics",
        "id": ",".join(video_ids[:50]),
        "key": api_key,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params=params)
        resp.raise_for_status()
        data = resp.json()

    return data.get("items", [])


def _items_to_shorts(items: list[dict[str, Any]]) -> list[YouTubeShort]:
    """Map raw API items → YouTubeShort dataclasses, filter to <60s."""
    shorts: list[YouTubeShort] = []
    for item in items:
        content = item.get("contentDetails", {})
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})

        duration_seconds = parse_iso8601_duration(content.get("duration", ""))
        if duration_seconds == 0 or duration_seconds > SHORTS_MAX_DURATION_SECONDS:
            continue  # not a Short

        try:
            view_count = int(stats.get("viewCount", "0"))
        except (TypeError, ValueError):
            view_count = 0

        shorts.append(
            YouTubeShort(
                video_id=item.get("id", ""),
                title=snippet.get("title", ""),
                channel_title=snippet.get("channelTitle", ""),
                description=snippet.get("description", ""),
                duration_seconds=duration_seconds,
                view_count=view_count,
                published_at=snippet.get("publishedAt", ""),
                tags=snippet.get("tags", []) or [],
            )
        )
    return shorts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def search_youtube_shorts(
    query: str,
    max_results: int = DEFAULT_SEARCH_MAX_RESULTS,
    api_key: str | None = None,
) -> list[YouTubeShort]:
    """Search YouTube for Shorts (<60s) matching the query.

    Args:
        query: search query, e.g. "Goa December nightlife shorts"
        max_results: how many search results to request (capped to 50 by API)
        api_key: override; defaults to settings.YOUTUBE_API_KEY

    Returns:
        List of YouTubeShort, filtered strictly to duration <= 60 seconds.

    Raises:
        RuntimeError if no API key is configured.
        httpx.HTTPStatusError for API errors (caller handles fallback).
    """
    key = api_key or settings.YOUTUBE_API_KEY
    if not key:
        raise RuntimeError(
            "YOUTUBE_API_KEY is not set — cannot run YouTube Shorts search."
        )

    logger.info("youtube.search query=%r max=%d", query, max_results)
    video_ids = await _search_short_videos(query, max_results, key)
    if not video_ids:
        logger.info("youtube.search returned 0 video ids")
        return []

    items = await _fetch_video_details(video_ids, key)
    shorts = _items_to_shorts(items)
    logger.info(
        "youtube.search filtered %d/%d items to actual Shorts (<60s)",
        len(shorts),
        len(items),
    )
    return shorts


# ---------------------------------------------------------------------------
# Optional: transcript fetch (best-effort, often unavailable for Shorts)
# ---------------------------------------------------------------------------


def fetch_transcript_safe(video_id: str) -> str | None:
    """Best-effort transcript fetch via youtube-transcript-api.

    Returns concatenated text on success, None on any failure.
    Most Shorts don't have manual captions and auto-captions can be poor —
    this is intentionally non-blocking.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    try:
        # API: get_transcript(video_id) → list of {"text": ..., "start": ..., "duration": ...}
        chunks = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
        return " ".join(c.get("text", "") for c in chunks).strip() or None
    except Exception as e:  # noqa: BLE001
        logger.debug("transcript fetch failed for %s: %s", video_id, e)
        return None
