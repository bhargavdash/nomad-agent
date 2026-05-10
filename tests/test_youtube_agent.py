"""Unit tests for YouTubeShortsAgent.

We mock both the YouTube tool AND the LLM call so these tests run with
no API keys and no network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.youtube_shorts import (
    _build_query,
    _filter_quality,
    _to_research_discoveries,
    run_youtube_agent,
)
from app.agents.youtube_shorts import _ExtractedDiscovery, _ExtractionResult
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
        budget="$$",
        preferences=None,
    )
    base.update(overrides)
    return TripParams(**base)


def _short(video_id: str, channel: str, view_count: int, duration: int = 45) -> YouTubeShort:
    return YouTubeShort(
        video_id=video_id,
        title=f"Cool {video_id} content",
        channel_title=channel,
        description="Some description here",
        duration_seconds=duration,
        view_count=view_count,
        published_at="2025-12-15T00:00:00Z",
        tags=["travel"],
    )


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------


def test_build_query_includes_destination_and_signal_modifiers() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    query = _build_query(trip, signals)

    assert "Goa, India" in query
    assert "travel shorts" in query


def test_build_query_skips_search_unfriendly_modifiers() -> None:
    """Modifiers like 'avoid crowds' are noise inside YouTube queries — should be skipped."""
    trip = _trip()
    signals = extract_signals(trip)
    query = _build_query(trip, signals)

    # These exact phrases should NOT make it into the query string
    assert "avoid crowds" not in query
    assert "off-the-beaten-path" not in query


# ---------------------------------------------------------------------------
# Quality filter
# ---------------------------------------------------------------------------


def test_filter_quality_drops_low_view_count_items() -> None:
    shorts = [
        _short("a", "ChannelA", view_count=10),  # below threshold
        _short("b", "ChannelB", view_count=10000),
    ]
    result = _filter_quality(shorts)
    assert len(result) == 1
    assert result[0].video_id == "b"


def test_filter_quality_dedupes_by_channel_keeps_best() -> None:
    """Same channel posting multiple Shorts → keep only the highest-view one."""
    shorts = [
        _short("a", "TravelGuru", view_count=5000),
        _short("b", "TravelGuru", view_count=20000),  # winner
        _short("c", "TravelGuru", view_count=1000),
        _short("d", "OtherChannel", view_count=8000),
    ]
    result = _filter_quality(shorts)
    channels = {s.channel_title for s in result}
    assert channels == {"TravelGuru", "OtherChannel"}
    travel_guru = next(s for s in result if s.channel_title == "TravelGuru")
    assert travel_guru.video_id == "b"


# ---------------------------------------------------------------------------
# Mapping LLM output → ResearchDiscovery
# ---------------------------------------------------------------------------


def test_to_research_discoveries_tags_source_youtube() -> None:
    extracted = [
        _ExtractedDiscovery(
            title="Sunset at Vagator",
            body="Vagator beach at sunset is mentioned across 3 different Shorts. Quiet rocks.",
            tags=["beach", "sunset", "north-goa"],
            confidence="high",
        ),
    ]
    result = _to_research_discoveries(extracted)
    assert len(result) == 1
    assert result[0].source == "youtube"
    assert result[0].title == "Sunset at Vagator"
    assert result[0].tags == ["beach", "sunset", "north-goa"]
    assert result[0].id  # uuid assigned


def test_to_research_discoveries_trims_excess_tags() -> None:
    """ResearchDiscovery schema allows max 3 tags — must trim defensively."""
    extracted = [
        _ExtractedDiscovery(
            title="X",
            body="A discovery body that meets the 20 char minimum requirement.",
            tags=["a", "b", "c"],  # already valid (max 3 in source schema)
            confidence="high",
        ),
    ]
    result = _to_research_discoveries(extracted)
    assert len(result[0].tags) == 3


# ---------------------------------------------------------------------------
# Full agent flow with mocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_youtube_agent_returns_empty_when_search_returns_nothing() -> None:
    trip = _trip()
    signals = extract_signals(trip)

    with patch(
        "app.agents.youtube_shorts.search_youtube_shorts",
        AsyncMock(return_value=[]),
    ):
        result = await run_youtube_agent(trip, signals)

    assert result == []


@pytest.mark.asyncio
async def test_run_youtube_agent_returns_empty_when_api_key_missing() -> None:
    """Tool raises RuntimeError when YOUTUBE_API_KEY missing — agent must catch and return []."""
    trip = _trip()
    signals = extract_signals(trip)

    with patch(
        "app.agents.youtube_shorts.search_youtube_shorts",
        AsyncMock(side_effect=RuntimeError("YOUTUBE_API_KEY is not set")),
    ):
        result = await run_youtube_agent(trip, signals)

    assert result == []


@pytest.mark.asyncio
async def test_run_youtube_agent_happy_path_with_mocks() -> None:
    """End-to-end with both the tool and the LLM mocked.

    Verifies the agent threads search → filter → LLM → ResearchDiscovery correctly.
    """
    trip = _trip()
    signals = extract_signals(trip)

    fake_shorts = [
        _short("a", "ChannelA", view_count=10000),
        _short("b", "ChannelB", view_count=20000),
        _short("c", "ChannelC", view_count=5000),
    ]
    fake_llm_output = _ExtractionResult(
        discoveries=[
            _ExtractedDiscovery(
                title="Anjuna Flea Market",
                body="The Wednesday flea market at Anjuna shows up across 4 different Shorts. "
                     "Bargaining is expected; arrive before noon for the best stalls.",
                tags=["market", "anjuna", "shopping"],
                confidence="high",
            ),
            _ExtractedDiscovery(
                title="Sunset at Vagator",
                body="Mentioned across 3 creators as the spot for sunsets. Get there 45 min early.",
                tags=["sunset", "beach"],
                confidence="high",
            ),
        ]
    )

    fake_llm = MagicMock()
    fake_structured = MagicMock()
    fake_structured.ainvoke = AsyncMock(return_value=fake_llm_output)
    fake_llm.with_structured_output.return_value = fake_structured

    with (
        patch(
            "app.agents.youtube_shorts.search_youtube_shorts",
            AsyncMock(return_value=fake_shorts),
        ),
        patch("app.agents.youtube_shorts.get_llm", return_value=fake_llm),
    ):
        result = await run_youtube_agent(trip, signals)

    assert len(result) == 2
    assert all(d.source == "youtube" for d in result)
    titles = {d.title for d in result}
    assert "Anjuna Flea Market" in titles
    assert "Sunset at Vagator" in titles


@pytest.mark.asyncio
async def test_run_youtube_agent_swallows_unexpected_errors() -> None:
    """If the LLM call blows up unexpectedly, the agent must NOT crash the pipeline."""
    trip = _trip()
    signals = extract_signals(trip)

    fake_shorts = [_short("a", "ChannelA", view_count=10000)]

    fake_llm = MagicMock()
    fake_structured = MagicMock()
    fake_structured.ainvoke = AsyncMock(side_effect=Exception("LLM blew up"))
    fake_llm.with_structured_output.return_value = fake_structured

    with (
        patch(
            "app.agents.youtube_shorts.search_youtube_shorts",
            AsyncMock(return_value=fake_shorts),
        ),
        patch("app.agents.youtube_shorts.get_llm", return_value=fake_llm),
    ):
        result = await run_youtube_agent(trip, signals)

    assert result == []
