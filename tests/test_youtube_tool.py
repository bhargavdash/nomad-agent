"""Unit tests for the YouTube Data API tool layer.

No real API calls — uses httpx.MockTransport to verify that:
  - We hit the correct endpoints with the correct params
  - ISO 8601 durations parse correctly
  - The short-form ceiling (<=300s) drops only over-long videos
  - Empty / malformed responses don't crash
"""

from __future__ import annotations


import httpx
import pytest

from app.tools import youtube as yt
from app.tools.youtube import (
    YouTubeShort,
    parse_iso8601_duration,
    search_youtube_shorts,
)


def test_parse_iso8601_seconds_only() -> None:
    assert parse_iso8601_duration("PT45S") == 45


def test_parse_iso8601_minutes_seconds() -> None:
    assert parse_iso8601_duration("PT1M30S") == 90


def test_parse_iso8601_hours_minutes_seconds() -> None:
    assert parse_iso8601_duration("PT1H2M3S") == 3723


def test_parse_iso8601_invalid_returns_zero() -> None:
    assert parse_iso8601_duration("garbage") == 0
    assert parse_iso8601_duration("") == 0


def test_iso8601_minute_only() -> None:
    assert parse_iso8601_duration("PT2M") == 120


# ---------------------------------------------------------------------------
# Mocked end-to-end search test
# ---------------------------------------------------------------------------


def _build_mock_transport(search_payload: dict, videos_payload: dict) -> httpx.MockTransport:
    """Return an httpx MockTransport that responds to /search and /videos."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search" in request.url.path:
            return httpx.Response(200, json=search_payload)
        if "/videos" in request.url.path:
            return httpx.Response(200, json=videos_payload)
        return httpx.Response(404, json={"error": "not mocked"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_search_youtube_shorts_keeps_short_form_drops_overlong(monkeypatch) -> None:
    """45s and 200s should survive (short-form). 6-minute item should be dropped."""

    search_payload = {
        "items": [
            {"id": {"videoId": "abc"}},
            {"id": {"videoId": "def"}},
            {"id": {"videoId": "ghi"}},
        ]
    }
    videos_payload = {
        "items": [
            {
                "id": "abc",
                "contentDetails": {"duration": "PT45S"},
                "snippet": {
                    "title": "Goa hidden beach",
                    "channelTitle": "TravelNomad",
                    "description": "This secret beach in north Goa is unreal!",
                    "publishedAt": "2025-12-15T00:00:00Z",
                    "tags": ["goa", "beach"],
                },
                "statistics": {"viewCount": "12000", "likeCount": "240"},
            },
            {
                "id": "def",
                "contentDetails": {"duration": "PT3M20S"},  # 200s — kept (short-form)
                "snippet": {
                    "title": "POV cabo de rama",
                    "channelTitle": "Vlogger",
                    "description": "...",
                    "publishedAt": "2025-12-10T00:00:00Z",
                },
                "statistics": {"viewCount": "5000", "likeCount": "100"},
            },
            {
                "id": "ghi",
                "contentDetails": {"duration": "PT6M00S"},  # 360s — dropped
                "snippet": {
                    "title": "Long travel guide",
                    "channelTitle": "Guide",
                    "description": "...",
                    "publishedAt": "2025-12-09T00:00:00Z",
                },
                "statistics": {"viewCount": "2000"},
            },
        ]
    }

    transport = _build_mock_transport(search_payload, videos_payload)

    import httpx as real_httpx

    real_async_client = real_httpx.AsyncClient

    def patched_async_client(*args, **kwargs):  # noqa: ANN001
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(yt.httpx, "AsyncClient", patched_async_client)

    result = await search_youtube_shorts("Goa shorts", max_results=10, api_key="fake-key")

    ids = {s.video_id for s in result}
    assert ids == {"abc", "def"}
    abc = next(s for s in result if s.video_id == "abc")
    assert isinstance(abc, YouTubeShort)
    assert abc.duration_seconds == 45
    assert abc.view_count == 12000
    assert abc.like_count == 240
    assert abc.url == "https://www.youtube.com/shorts/abc"


@pytest.mark.asyncio
async def test_search_youtube_shorts_handles_empty_response(monkeypatch) -> None:
    transport = _build_mock_transport({"items": []}, {"items": []})

    import httpx as real_httpx

    real_async_client = real_httpx.AsyncClient

    def patched(*args, **kwargs):  # noqa: ANN001
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(yt.httpx, "AsyncClient", patched)

    result = await search_youtube_shorts("xyz", max_results=10, api_key="fake-key")
    assert result == []


@pytest.mark.asyncio
async def test_search_youtube_shorts_raises_without_api_key(monkeypatch) -> None:
    # Force the settings fallback to empty so the test is deterministic whether
    # or not a local .env provides YOUTUBE_API_KEY (CI has none; dev usually
    # does). Without this, `api_key=""` still falls back to settings.
    monkeypatch.setattr(yt.settings, "YOUTUBE_API_KEY", "")
    with pytest.raises(RuntimeError, match="YOUTUBE_API_KEY"):
        await search_youtube_shorts("anything", api_key="")
