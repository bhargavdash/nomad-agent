"""YouTube Data API v3 tool — fetch authentic short-form travel content.

Why short-form (≤5 min) and not long-form vlogs?
  Long-form travel YouTube is over-produced and sponsored. Short-form
  tends to capture authentic "I just walked into this place" moments.
  Cross-creator aggregation (same place mentioned across 3+ Shorts) is
  a strong quality signal.

Duration policy:
  We use the API's videoDuration=short filter (≤4 min per Google) but
  also keep videos up to SHORTS_MAX_DURATION_SECONDS (default 300s) so
  shorts-style 60–180s vlogs are not lost. The strict <60s filter was
  removed: it threw away authentic POV vlogs that name specific places.
  Items with 0/unknown duration are kept (some endpoints omit it).

Quota:
  search.list = 100 units, videos.list = 1 unit per call.
  Free tier = 10,000 units/day. One agent run with 5 fan-out queries ≈
  505 units (5 × search + ~5 × videos.list). Still plenty of headroom.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
# Soft upper bound for "short-form" content. We deliberately exceed the
# strict <60s YouTube Shorts cutoff so authentic 1–5 min POV vlogs are kept.
SHORTS_MAX_DURATION_SECONDS = 300
# Long-form vlog window. Below 240s overlaps with Shorts agent's beat; above
# 1500s skews into documentary / sponsored / full-trip-recap territory that
# the Shorts-side comments call "over-produced and sponsored". 4-25 min is
# the sweet spot for authentic creator vlogs with mandatory captions.
LONGFORM_MIN_DURATION_SECONDS = 240
LONGFORM_MAX_DURATION_SECONDS = 1500
DEFAULT_SEARCH_MAX_RESULTS = 25

# Freshness window for YouTube searches. 2 years drops most pre-pandemic
# travel content (where attractions/restaurants frequently no longer exist
# or have changed character) while keeping a reasonable depth of authentic
# creator content. Iconic landmarks like the Eiffel Tower still have plenty
# of recent content; this isn't a coverage problem for major anchors.
PUBLISHED_AFTER_DAYS = 730


def _published_after_iso() -> str:
    """RFC 3339 timestamp PUBLISHED_AFTER_DAYS ago, for YouTube's publishedAfter
    parameter. Computed at call time so each request uses a current cutoff."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PUBLISHED_AFTER_DAYS)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    like_count: int
    published_at: str  # ISO 8601
    tags: list[str]
    transcript: str | None = None  # populated lazily by the agent, optional

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/shorts/{self.video_id}"

    @property
    def like_view_ratio(self) -> float:
        """Engagement proxy. >=0.01 (1%) is healthy on Shorts; viral mass-market
        content often falls below 0.5%."""
        if self.view_count <= 0:
            return 0.0
        return self.like_count / self.view_count


# ---------------------------------------------------------------------------
# ISO 8601 duration parser (YouTube returns durations like "PT45S", "PT1M30S")
# ---------------------------------------------------------------------------

_ISO8601_RE = re.compile(r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$")


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


async def _search_short_videos(query: str, max_results: int, api_key: str) -> list[str]:
    """Call search.list with videoDuration=short. Returns a list of videoIds."""
    params = {
        "part": "id",
        "q": query,
        "type": "video",
        "videoDuration": "short",  # <4 min — we re-filter to <60s below
        "maxResults": min(max_results, 50),
        "order": "relevance",
        "safeSearch": "moderate",
        "publishedAfter": _published_after_iso(),
        "key": api_key,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{YOUTUBE_API_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()

    return [
        item["id"]["videoId"] for item in data.get("items", []) if item.get("id", {}).get("videoId")
    ]


async def _fetch_video_details(video_ids: list[str], api_key: str) -> list[dict[str, Any]]:
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
        # Drop only items that exceed our short-form ceiling. Items with
        # duration_seconds == 0 (parse failed / live / missing) are kept;
        # the agent's quality filters and LLM stage will sort them out.
        if duration_seconds > SHORTS_MAX_DURATION_SECONDS:
            continue

        try:
            view_count = int(stats.get("viewCount", "0"))
        except (TypeError, ValueError):
            view_count = 0
        try:
            like_count = int(stats.get("likeCount", "0"))
        except (TypeError, ValueError):
            like_count = 0

        shorts.append(
            YouTubeShort(
                video_id=item.get("id", ""),
                title=snippet.get("title", ""),
                channel_title=snippet.get("channelTitle", ""),
                description=snippet.get("description", ""),
                duration_seconds=duration_seconds,
                view_count=view_count,
                like_count=like_count,
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
        raise RuntimeError("YOUTUBE_API_KEY is not set — cannot run YouTube Shorts search.")

    logger.info("youtube.search query=%r max=%d", query, max_results)
    video_ids = await _search_short_videos(query, max_results, key)
    if not video_ids:
        logger.info("youtube.search returned 0 video ids")
        return []

    items = await _fetch_video_details(video_ids, key)
    shorts = _items_to_shorts(items)
    logger.info(
        "youtube.search kept %d/%d short-form items (<=%ds)",
        len(shorts),
        len(items),
        SHORTS_MAX_DURATION_SECONDS,
    )
    return shorts


# ---------------------------------------------------------------------------
# Long-form variant (4-25 min creator vlogs). Same dataclass shape as Shorts —
# only the search filter (`videoDuration=medium`) and the post-filter window
# differ. Reused by the long-form agent; not used by the Shorts agent.
# ---------------------------------------------------------------------------


async def _search_medium_videos(query: str, max_results: int, api_key: str) -> list[str]:
    """search.list with videoDuration=medium (4-20 min by YouTube definition)."""
    params = {
        "part": "id",
        "q": query,
        "type": "video",
        "videoDuration": "medium",
        "maxResults": min(max_results, 50),
        "order": "relevance",
        "safeSearch": "moderate",
        "publishedAfter": _published_after_iso(),
        "key": api_key,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{YOUTUBE_API_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()
    return [
        item["id"]["videoId"] for item in data.get("items", []) if item.get("id", {}).get("videoId")
    ]


def _items_to_longform(items: list[dict[str, Any]]) -> list[YouTubeShort]:
    """Map raw API items → YouTubeShort dataclasses, filtered to long-form window.

    Reuses YouTubeShort so the downstream extraction pipeline (transcript fetch,
    Pass-1/2 LLM, clustering) doesn't need a parallel data type. The
    `duration_seconds` field tells the agent which window the video came from.
    """
    out: list[YouTubeShort] = []
    for item in items:
        content = item.get("contentDetails", {})
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        duration_seconds = parse_iso8601_duration(content.get("duration", ""))
        # Drop items outside the long-form window. Unknown duration (0) gets
        # dropped here too — unlike Shorts, long-form needs reliable duration
        # because we're tuning transcript fetch + engagement thresholds to it.
        if duration_seconds < LONGFORM_MIN_DURATION_SECONDS:
            continue
        if duration_seconds > LONGFORM_MAX_DURATION_SECONDS:
            continue
        try:
            view_count = int(stats.get("viewCount", "0"))
        except (TypeError, ValueError):
            view_count = 0
        try:
            like_count = int(stats.get("likeCount", "0"))
        except (TypeError, ValueError):
            like_count = 0
        out.append(
            YouTubeShort(
                video_id=item.get("id", ""),
                title=snippet.get("title", ""),
                channel_title=snippet.get("channelTitle", ""),
                description=snippet.get("description", ""),
                duration_seconds=duration_seconds,
                view_count=view_count,
                like_count=like_count,
                published_at=snippet.get("publishedAt", ""),
                tags=snippet.get("tags", []) or [],
            )
        )
    return out


async def search_youtube_longform(
    query: str,
    max_results: int = DEFAULT_SEARCH_MAX_RESULTS,
    api_key: str | None = None,
) -> list[YouTubeShort]:
    """Search for long-form creator vlogs (4-25 min) matching the query.

    Unlike `search_youtube_shorts`, transcripts are typically present and
    are a hard requirement at the agent layer (long-form without captions
    is unusable — there's no way to extract place mentions cheaply).

    Returns YouTubeShort dataclasses for downstream pipeline reuse.
    """
    key = api_key or settings.YOUTUBE_API_KEY
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY is not set — cannot run YouTube long-form search.")
    logger.info("youtube.longform.search query=%r max=%d", query, max_results)
    video_ids = await _search_medium_videos(query, max_results, key)
    if not video_ids:
        logger.info("youtube.longform.search returned 0 video ids")
        return []
    items = await _fetch_video_details(video_ids, key)
    longform = _items_to_longform(items)
    logger.info(
        "youtube.longform.search kept %d/%d items (in [%d,%d]s)",
        len(longform),
        len(items),
        LONGFORM_MIN_DURATION_SECONDS,
        LONGFORM_MAX_DURATION_SECONDS,
    )
    return longform


# ---------------------------------------------------------------------------
# Optional: transcript fetch (best-effort, often unavailable for Shorts)
# ---------------------------------------------------------------------------


def fetch_transcript_safe(video_id: str, max_chars: int = 800) -> str | None:
    """Best-effort transcript fetch via youtube-transcript-api.

    Sync function — call from async code via `await asyncio.to_thread(...)`.
    Returns concatenated text (truncated to max_chars) on success, None on
    any failure. Many Shorts have no captions; failure is the common case.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    try:
        # The library exposes get_transcript on older versions and
        # YouTubeTranscriptApi().fetch on 1.x — try both.
        text: str | None = None
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            chunks = YouTubeTranscriptApi.get_transcript(  # type: ignore[attr-defined]
                video_id, languages=["en", "en-US", "en-GB"]
            )
            text = " ".join(c.get("text", "") for c in chunks).strip()
        else:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
            # FetchedTranscript has .snippets[*].text
            text = " ".join(s.text for s in fetched.snippets).strip()  # type: ignore[attr-defined]
        if not text:
            return None
        # Collapse whitespace + trim
        text = " ".join(text.split())
        return text[:max_chars] + ("…" if len(text) > max_chars else "")
    except Exception as e:  # noqa: BLE001
        logger.debug("transcript fetch failed for %s: %s", video_id, e)
        return None
