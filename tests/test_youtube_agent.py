"""Unit tests for YouTubeShortsAgent.

We mock both the YouTube tool AND the LLM call so these tests run with
no API keys and no network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.youtube_shorts import (
    _ExtractedDiscovery,
    _build_queries,
    _build_query,
    _filter_quality,
    _is_listicle,
    _passes_engagement,
    _to_research_discoveries,
    _validate_and_dedupe,
    run_youtube_agent,
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


def _short(
    video_id: str,
    channel: str,
    view_count: int,
    *,
    title: str | None = None,
    duration: int = 90,
    like_count: int | None = None,
) -> YouTubeShort:
    if like_count is None:
        # Default to a healthy 1% like:view ratio so engagement filter passes.
        like_count = max(view_count // 100, 1)
    return YouTubeShort(
        video_id=video_id,
        title=title or f"Cool {video_id} content",
        channel_title=channel,
        description="Some description here",
        duration_seconds=duration,
        view_count=view_count,
        like_count=like_count,
        published_at="2025-12-15T00:00:00Z",
        tags=["travel"],
    )


def _disc(
    place_name: str = "Anjuna Flea Market",
    *,
    why: str = "The Wednesday flea market at Anjuna with bargaining and live music.",
    evidence: list[int] | None = None,
    confidence: str = "high",
    tags: list[str] | None = None,
) -> _ExtractedDiscovery:
    return _ExtractedDiscovery(
        place_name=place_name,
        why_specific=why,
        evidence_short_indices=evidence if evidence is not None else [1, 2],
        tags=tags or ["market", "anjuna"],
        confidence=confidence,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Query builder — Layer 1b
# ---------------------------------------------------------------------------


def test_build_queries_returns_destination_agnostic_queries() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    queries = _build_queries(trip, signals)

    # Always present axes.
    assert any("travel vlog" in q for q in queries)
    assert any("food" in q for q in queries)
    # Sprint 5: anchor query replaces the prior "hidden places" slot so famous
    # attractions surface alongside niche-creator content.
    assert any("top things to do" in q for q in queries)
    # Each query must reference the destination.
    assert all("Goa, India" in q for q in queries)
    # Capped at 5.
    assert 3 <= len(queries) <= 5


def test_build_queries_uses_first_vibe_when_present() -> None:
    trip = _trip(vibes=["nightlife", "beaches"])
    signals = extract_signals(trip)
    queries = _build_queries(trip, signals)
    assert any(q.endswith("nightlife") for q in queries)


def test_build_queries_falls_back_when_no_vibes() -> None:
    trip = _trip(vibes=[])
    signals = extract_signals(trip)
    queries = _build_queries(trip, signals)
    # Should include the generic discovery prompt or the anchor query.
    assert any("things to do in" in q for q in queries)


def test_build_queries_includes_season_only_when_informative() -> None:
    # Goa in late June → monsoon, season should appear.
    trip = _trip(date_from="2026-06-15", date_to="2026-06-22")
    signals = extract_signals(trip)
    queries = _build_queries(trip, signals)
    assert any("monsoon" in q for q in queries)


def test_build_queries_works_for_any_destination() -> None:
    """No Goa-specific hardcoding — must work worldwide."""
    trip = _trip(destination="Reykjavik, Iceland", vibes=["northern lights"])
    signals = extract_signals(trip)
    queries = _build_queries(trip, signals)
    assert all("Reykjavik, Iceland" in q for q in queries)
    assert any("northern lights" in q for q in queries)


def test_build_query_back_compat_returns_first_query() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    assert _build_query(trip, signals) == _build_queries(trip, signals)[0]


# ---------------------------------------------------------------------------
# Listicle / clickbait filter — Layer 1d
# ---------------------------------------------------------------------------


def test_listicle_filter_drops_top_n_titles() -> None:
    assert _is_listicle("Top 10 Places to Visit in Goa")
    assert _is_listicle("Best 5 beaches in Goa")
    assert _is_listicle("Goa Tourist Places You MUST Visit")
    assert _is_listicle("5 things to do in Goa")


def test_listicle_filter_keeps_authentic_titles() -> None:
    assert not _is_listicle("Eating crab at Martin's Corner Goa")
    assert not _is_listicle("Cabo de Rama fort in monsoon")
    assert not _is_listicle("Sunday brunch at Bomras")


# ---------------------------------------------------------------------------
# Engagement filter — Layer 1e
# ---------------------------------------------------------------------------


def test_engagement_filter_drops_below_view_floor() -> None:
    assert not _passes_engagement(_short("a", "C", view_count=100))


def test_engagement_filter_drops_low_like_ratio() -> None:
    # 1000 views, 1 like → 0.1% ratio (well under 0.3% floor).
    s = _short("a", "C", view_count=1000, like_count=1)
    assert not _passes_engagement(s)


def test_engagement_filter_keeps_hidden_likes() -> None:
    """Some videos hide likes (like_count = 0). Don't penalize those."""
    s = _short("a", "C", view_count=10000, like_count=0)
    assert _passes_engagement(s)


# ---------------------------------------------------------------------------
# Quality filter (composes listicle + engagement + per-channel-best)
# ---------------------------------------------------------------------------


def test_filter_quality_deprioritizes_listicles_but_keeps_them() -> None:
    """Sprint 5: listicle videos are kept (anchor-attraction content lives in
    'Top 10' titles) but ranked below non-listicle within the same view tier."""
    shorts = [
        _short("a", "C1", view_count=10000, title="Top 10 Places in Goa"),
        _short("b", "C2", view_count=10000, title="Crab xacuti at Martin's Corner"),
    ]
    result = _filter_quality(shorts)
    # Both survive — anchor coverage requires keeping listicle-format videos.
    assert {s.video_id for s in result} == {"a", "b"}
    # Non-listicle ranks first within the same view-count tier.
    assert result[0].video_id == "b"


def test_filter_quality_caps_per_channel_keeps_top_two() -> None:
    """Sprint 5: per-channel cap raised from 1 to 2 so a channel with multiple
    relevant videos can contribute more than one anchor."""
    shorts = [
        _short("a", "TravelGuru", view_count=5000),
        _short("b", "TravelGuru", view_count=20000),  # winner
        _short("c", "TravelGuru", view_count=1000),  # third — dropped
        _short("d", "OtherChannel", view_count=8000),
    ]
    result = _filter_quality(shorts)
    channels = {s.channel_title for s in result}
    assert channels == {"TravelGuru", "OtherChannel"}
    travel_guru_ids = {s.video_id for s in result if s.channel_title == "TravelGuru"}
    # Top 2 of the 3 TravelGuru videos: b (20k) and a (5k); c (1k) dropped.
    assert travel_guru_ids == {"a", "b"}


# ---------------------------------------------------------------------------
# Validate + dedupe — Layer 2a / 2d / 2e
# ---------------------------------------------------------------------------


def test_validate_drops_generic_title() -> None:
    extracted = [
        _disc(place_name="North Goa"),  # generic
        _disc(place_name="Cabo de Rama Fort"),
    ]
    survivors = _validate_and_dedupe(extracted, n_videos=5)
    assert [d.place_name for d in survivors] == ["Cabo de Rama Fort"]


def test_validate_drops_vague_body() -> None:
    extracted = [
        _disc(
            place_name="Some Real Beach",
            why="It has stunning beaches and vibrant culture.",
        ),
        _disc(
            place_name="Bar do Mineiro",
            why="A small bar in old quarter known for pão de queijo and cachaça.",
        ),
    ]
    survivors = _validate_and_dedupe(extracted, n_videos=5)
    assert [d.place_name for d in survivors] == ["Bar do Mineiro"]


def test_validate_drops_no_evidence() -> None:
    """Schema requires min_length=1, but indices outside range count as no-evidence."""
    extracted = [
        _disc(place_name="Phantom Spot", evidence=[99]),  # out of range for n_videos=5
        _disc(place_name="Real Spot", evidence=[1, 2]),
    ]
    survivors = _validate_and_dedupe(extracted, n_videos=5)
    assert [d.place_name for d in survivors] == ["Real Spot"]


def test_validate_dedupes_by_place_name() -> None:
    extracted = [
        _disc(place_name="Cabo de Rama Fort", evidence=[1], confidence="medium"),
        _disc(place_name="cabo de rama fort", evidence=[1, 2, 4], confidence="high"),
    ]
    survivors = _validate_and_dedupe(extracted, n_videos=5)
    assert len(survivors) == 1
    # The richer (more evidence, higher confidence) entry should win.
    assert survivors[0].confidence == "high"
    assert len(survivors[0].evidence_short_indices) == 3


# ---------------------------------------------------------------------------
# Mapping → ResearchDiscovery
# ---------------------------------------------------------------------------


def test_to_research_discoveries_combines_fields() -> None:
    extracted = [
        _ExtractedDiscovery(
            place_name="Butter Café, Assagao",
            why_specific="Specialty café in north Goa villages, known for cinnamon rolls.",
            best_time="before 10am",
            practical_tip="₹250–400 per item",
            evidence_short_indices=[3, 7],
            tags=["cafe", "assagao", "breakfast"],
            confidence="medium",
        )
    ]
    result = _to_research_discoveries(extracted)
    assert len(result) == 1
    d = result[0]
    assert d.source == "youtube"
    assert d.title == "Butter Café, Assagao"
    assert "before 10am" in d.body
    assert "₹250" in d.body
    assert d.tags == ["cafe", "assagao", "breakfast"]
    assert d.id


# ---------------------------------------------------------------------------
# Full agent flow with mocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_youtube_agent_returns_empty_when_search_returns_nothing() -> None:
    trip = _trip()
    signals = extract_signals(trip)

    with (
        patch(
            "app.agents.youtube_shorts.search_youtube_shorts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.agents.youtube_shorts.fetch_transcript_safe",
            return_value=None,
        ),
    ):
        result = await run_youtube_agent(trip, signals)

    assert result == []


@pytest.mark.asyncio
async def test_run_youtube_agent_returns_empty_when_api_key_missing() -> None:
    trip = _trip()
    signals = extract_signals(trip)

    with patch(
        "app.agents.youtube_shorts.search_youtube_shorts",
        AsyncMock(side_effect=RuntimeError("YOUTUBE_API_KEY is not set")),
    ):
        result = await run_youtube_agent(trip, signals)

    # All fan-out queries fail with RuntimeError → no shorts → []
    assert result == []


@pytest.mark.asyncio
async def test_run_youtube_agent_happy_path_with_mocks() -> None:
    """End-to-end: search returns shorts, _extract_via_llm produces discoveries,
    and the run loop wires them through validation → ResearchDiscovery."""
    trip = _trip()
    signals = extract_signals(trip)

    fake_shorts = [
        _short("a", "ChannelA", view_count=10000),
        _short("b", "ChannelB", view_count=20000),
        _short("c", "ChannelC", view_count=5000),
    ]
    extracted = [
        _ExtractedDiscovery(
            place_name="Anjuna Flea Market",
            why_specific=(
                "Wednesday flea market at Anjuna with bargaining and live music. "
                "Arrive before noon for best stalls."
            ),
            best_time="before noon",
            practical_tip=None,
            evidence_short_indices=[1, 2],
            tags=["market", "anjuna", "shopping"],
            confidence="high",
        ),
        _ExtractedDiscovery(
            place_name="Cabo de Rama Fort",
            why_specific=(
                "Clifftop ruins on the south coast, quieter alternative to Chapora. Free entry."
            ),
            best_time="sunset",
            practical_tip="Free entry",
            evidence_short_indices=[3],
            tags=["fort", "south-goa", "viewpoint"],
            confidence="medium",
        ),
    ]

    with (
        patch(
            "app.agents.youtube_shorts.search_youtube_shorts",
            AsyncMock(return_value=fake_shorts),
        ),
        patch(
            "app.agents.youtube_shorts._extract_via_llm",
            AsyncMock(return_value=extracted),
        ),
        patch(
            "app.agents.youtube_shorts.fetch_transcript_safe",
            return_value=None,
        ),
    ):
        result = await run_youtube_agent(trip, signals)

    assert len(result) == 2
    assert all(d.source == "youtube" for d in result)
    titles = {d.title for d in result}
    assert "Anjuna Flea Market" in titles
    assert "Cabo de Rama Fort" in titles


@pytest.mark.asyncio
async def test_run_youtube_agent_drops_vague_llm_output() -> None:
    """Even if the LLM emits generic content, the validator must drop it."""
    trip = _trip()
    signals = extract_signals(trip)

    fake_shorts = [_short("a", "ChannelA", view_count=10000)]
    extracted = [
        _ExtractedDiscovery(
            place_name="North Goa",  # generic title — must be dropped
            why_specific="North Goa has stunning beaches and vibrant nightlife.",
            evidence_short_indices=[1],
            tags=["north-goa", "beach"],
            confidence="high",
        ),
    ]

    with (
        patch(
            "app.agents.youtube_shorts.search_youtube_shorts",
            AsyncMock(return_value=fake_shorts),
        ),
        patch(
            "app.agents.youtube_shorts._extract_via_llm",
            AsyncMock(return_value=extracted),
        ),
        patch(
            "app.agents.youtube_shorts.fetch_transcript_safe",
            return_value=None,
        ),
    ):
        result = await run_youtube_agent(trip, signals)

    assert result == []


@pytest.mark.asyncio
async def test_run_youtube_agent_swallows_unexpected_errors() -> None:
    trip = _trip()
    signals = extract_signals(trip)

    fake_shorts = [_short("a", "ChannelA", view_count=10000)]

    with (
        patch(
            "app.agents.youtube_shorts.search_youtube_shorts",
            AsyncMock(return_value=fake_shorts),
        ),
        patch(
            "app.agents.youtube_shorts._extract_via_llm",
            AsyncMock(side_effect=Exception("LLM blew up")),
        ),
        patch(
            "app.agents.youtube_shorts.fetch_transcript_safe",
            return_value=None,
        ),
    ):
        result = await run_youtube_agent(trip, signals)

    assert result == []


# ---------------------------------------------------------------------------
# Layer 3 — clustering helpers (pure, no LLM)
# ---------------------------------------------------------------------------


def test_normalize_place_key_collapses_punctuation_and_case() -> None:
    from app.agents.youtube_shorts import _normalize_place_key

    assert _normalize_place_key("Anjuna Flea Market") == "anjuna flea market"
    assert _normalize_place_key("Anjuna  Flea  Market!") == _normalize_place_key(
        "anjuna flea market"
    )


def test_cluster_mentions_groups_by_normalized_name() -> None:
    from app.agents.youtube_shorts import _PlaceMention, _cluster_mentions

    mentions = [
        _PlaceMention(
            video_index=1,
            place_name="Dudhsagar Falls",
            quote="Dudhsagar in monsoon",
            category="waterfall",
        ),
        _PlaceMention(
            video_index=4,
            place_name="dudhsagar falls",
            quote="Trip to Dudhsagar",
            category="waterfall",
        ),
        _PlaceMention(
            video_index=7,
            place_name="DUDHSAGAR FALLS!",
            quote="Iconic falls",
            category="waterfall",
        ),
        _PlaceMention(
            video_index=2,
            place_name="Anjuna Beach",
            quote="Anjuna parties",
            category="beach",
        ),
    ]
    clusters = _cluster_mentions(mentions, n_videos=10)
    assert len(clusters) == 2
    # Top cluster should be Dudhsagar (3 distinct videos).
    top_name, top_ms = clusters[0]
    assert top_name.lower().startswith("dudhsagar")
    assert {m.video_index for m in top_ms} == {1, 4, 7}


def test_cluster_mentions_drops_destination_clusters() -> None:
    """For trip to 'Rajasthan, India', a cluster named 'Rajasthan' or 'India'
    is too coarse and must be dropped. Sub-places like 'Hawa Mahal' survive."""
    from app.agents.youtube_shorts import _PlaceMention, _cluster_mentions

    mentions = [
        _PlaceMention(
            video_index=1,
            place_name="Rajasthan",
            quote="Rajasthan trip",
            category="region",
        ),
        _PlaceMention(
            video_index=2,
            place_name="India",
            quote="India travel",
            category="country",
        ),
        _PlaceMention(
            video_index=3,
            place_name="Hawa Mahal",
            quote="Hawa Mahal Jaipur",
            category="palace",
        ),
        _PlaceMention(
            video_index=4,
            place_name="Hawa Mahal",
            quote="iconic facade",
            category="palace",
        ),
    ]
    clusters = _cluster_mentions(mentions, n_videos=10, destination="Rajasthan, India")
    assert len(clusters) == 1
    assert clusters[0][0] == "Hawa Mahal"


def test_cluster_mentions_drops_generic_and_invalid_indices() -> None:
    from app.agents.youtube_shorts import _PlaceMention, _cluster_mentions

    mentions = [
        _PlaceMention(
            video_index=1,
            place_name="North Goa",  # generic — dropped
            quote="north goa vibe",
            category="neighborhood",
        ),
        _PlaceMention(
            video_index=99,  # out of range — dropped
            place_name="Cabo de Rama Fort",
            quote="clifftop",
            category="fort",
        ),
        _PlaceMention(
            video_index=2,
            place_name="Cabo de Rama Fort",
            quote="south coast",
            category="fort",
        ),
    ]
    clusters = _cluster_mentions(mentions, n_videos=5)
    assert len(clusters) == 1
    assert clusters[0][0] == "Cabo de Rama Fort"
