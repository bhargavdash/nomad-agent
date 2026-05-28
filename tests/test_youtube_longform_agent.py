"""Unit tests for YouTubeLongformAgent.

Mocks both the long-form YouTube tool AND the LLM call so these tests run
with no API keys and no network. Focuses on the differentiators from the
Shorts agent: mandatory-transcript gate, stricter listicle regex, channel
blacklist.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.youtube_longform import (
    LONGFORM_LISTICLE_TITLE_RE,
    _build_queries,
    _enrich_with_transcripts,
    _filter_quality,
    _is_blacklisted_channel,
    _is_listicle,
    _passes_engagement,
    run_youtube_longform_agent,
)
from app.schemas import TripParams
from app.signals import extract_signals
from app.tools.youtube import YouTubeShort


def _trip(**overrides) -> TripParams:
    base = dict(
        trip_id="t1",
        user_id="u1",
        destination="Goa, India",
        date_from="2026-12-25",
        date_to="2027-01-01",
        duration_days=7,
        travelers="2",
        vibes=["nightlife", "beaches"],
        accommodation="Hotel",
        pace="Balanced",
        budget="Medium",
        preferences=None,
    )
    base.update(overrides)
    return TripParams(**base)


def _video(
    video_id: str,
    channel: str,
    view_count: int,
    *,
    title: str | None = None,
    duration: int = 600,  # 10 min default — middle of the long-form window
    like_count: int | None = None,
    transcript: str | None = None,
) -> YouTubeShort:
    if like_count is None:
        like_count = max(view_count // 200, 1)  # ~0.5% — long-form healthy
    return YouTubeShort(
        video_id=video_id,
        title=title or f"Authentic {video_id} vlog",
        channel_title=channel,
        description="Trip vlog with lots of place mentions",
        duration_seconds=duration,
        view_count=view_count,
        like_count=like_count,
        published_at="2025-12-15T00:00:00Z",
        tags=["travel", "vlog"],
        transcript=transcript,
    )


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------


def test_build_queries_uses_long_form_flavoured_phrasing() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    queries = _build_queries(trip, signals)
    assert any("travel vlog" in q for q in queries)
    assert any("food guide" in q for q in queries)
    # Should NOT include the Shorts agent's "hidden places" suffix —
    # that biases toward Shorts even on a medium-duration search.
    assert not any("hidden places" in q for q in queries)
    # Should be capped at 4 queries.
    assert len(queries) <= 4


# ---------------------------------------------------------------------------
# Listicle regex — must be STRICTER than Shorts
# ---------------------------------------------------------------------------


def test_listicle_regex_catches_long_form_seo_patterns() -> None:
    bad_titles = [
        "Top 10 Things To Do in Goa",
        "ULTIMATE Guide to Manali",
        "Complete Guide to Rajasthan",
        "Everything You Need to Know About Bali",
        "Everything About Iceland in Winter",
        "10 Best Places to Visit in Goa",
        "Cheapest Way to Travel India",
        "Things to do in 3 Days in Goa",
    ]
    for t in bad_titles:
        assert _is_listicle(t), f"Should catch listicle: {t!r}"


def test_listicle_regex_keeps_authentic_creator_titles() -> None:
    good_titles = [
        "I spent a week in Anjuna and ate everything",
        "Goa with my parents — what surprised me",
        "Honest Manali vlog (we got rained on)",
        "Wandering Jaipur's pink alleys",
    ]
    for t in good_titles:
        assert not _is_listicle(t), f"Should NOT flag as listicle: {t!r}"


# ---------------------------------------------------------------------------
# Channel blacklist
# ---------------------------------------------------------------------------


def test_blacklisted_channels_are_dropped() -> None:
    assert _is_blacklisted_channel("TripAdvisor")
    assert _is_blacklisted_channel("Lonely Planet Travel Guides")
    assert _is_blacklisted_channel("Times of India Travel")
    assert _is_blacklisted_channel("NDTV Food")
    # Not blacklisted (random creator channels):
    assert not _is_blacklisted_channel("RandomTravelCreator")
    assert not _is_blacklisted_channel("Wayfaring Wanderer")


def test_filter_quality_drops_listicle_and_blacklisted() -> None:
    videos = [
        _video("v1", "AuthenticCreator", view_count=50_000),  # keep
        _video("v2", "TripAdvisor", view_count=500_000),  # drop (blacklist)
        _video(
            "v3", "OtherCreator", view_count=20_000, title="Top 10 Goa Beaches"
        ),  # drop (listicle)
        _video("v4", "ThirdCreator", view_count=200),  # drop (low views)
    ]
    survivors = _filter_quality(videos)
    survivor_ids = {v.video_id for v in survivors}
    assert "v1" in survivor_ids
    assert "v2" not in survivor_ids
    assert "v3" not in survivor_ids
    assert "v4" not in survivor_ids


# ---------------------------------------------------------------------------
# Engagement filter — long-form ratios are lower than Shorts
# ---------------------------------------------------------------------------


def test_engagement_filter_accepts_long_form_typical_ratios() -> None:
    # 0.2% like:view — would fail Shorts' 0.3% floor, passes long-form's 0.1%.
    v = _video("v", "creator", view_count=100_000, like_count=200)
    assert _passes_engagement(v)


def test_engagement_filter_rejects_below_view_floor() -> None:
    v = _video("v", "creator", view_count=500)  # < 1000 long-form floor
    assert not _passes_engagement(v)


# ---------------------------------------------------------------------------
# Mandatory-transcript gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_with_transcripts_drops_videos_without_captions() -> None:
    videos = [
        _video("v1", "c1", 10_000),
        _video("v2", "c2", 10_000),
        _video("v3", "c3", 10_000),
    ]

    # Mock the sync fetcher: v1 + v3 have transcripts, v2 doesn't.
    def fake_fetch(video_id: str, max_chars: int = 800) -> str | None:
        return "real transcript text" if video_id in {"v1", "v3"} else None

    with patch("app.agents.youtube_longform.fetch_transcript_safe", side_effect=fake_fetch):
        survivors = await _enrich_with_transcripts(videos)

    survivor_ids = {v.video_id for v in survivors}
    assert survivor_ids == {"v1", "v3"}
    assert all(v.transcript for v in survivors)


# ---------------------------------------------------------------------------
# Public entry point — graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_empty_when_no_videos_have_transcripts() -> None:
    """When every candidate fails the transcript gate, the agent returns []
    without ever calling the LLM (cost containment) and without raising."""
    trip = _trip()
    signals = extract_signals(trip)
    videos = [_video(f"v{i}", f"c{i}", 10_000) for i in range(3)]

    with (
        patch(
            "app.agents.youtube_longform._search_fanout",
            AsyncMock(return_value=videos),
        ),
        patch(
            "app.agents.youtube_longform.fetch_transcript_safe",
            return_value=None,  # nobody has captions
        ),
        patch(
            "app.agents.youtube_longform._extract_via_llm",
            AsyncMock(return_value=[]),
        ) as llm_mock,
    ):
        out = await run_youtube_longform_agent(trip, signals)

    assert out == []
    assert llm_mock.await_count == 0  # LLM never called


@pytest.mark.asyncio
async def test_run_returns_empty_on_zero_search_results() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    with patch(
        "app.agents.youtube_longform._search_fanout",
        AsyncMock(return_value=[]),
    ):
        out = await run_youtube_longform_agent(trip, signals)
    assert out == []


@pytest.mark.asyncio
async def test_run_catches_runtime_error_for_missing_api_key() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    with patch(
        "app.agents.youtube_longform._search_fanout",
        AsyncMock(side_effect=RuntimeError("YOUTUBE_API_KEY is not set")),
    ):
        out = await run_youtube_longform_agent(trip, signals)
    assert out == []  # Graceful degradation — synthesizer continues.


# Smoke check: the listicle regex compiles and is a re.Pattern.
def test_listicle_regex_is_compiled() -> None:
    import re

    assert isinstance(LONGFORM_LISTICLE_TITLE_RE, re.Pattern)
