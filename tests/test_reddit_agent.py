"""Unit tests for RedditAgent.

We mock the Reddit tool AND the LLM call so these run with no network
and no API keys.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.reddit import (
    _ExtractedInsight,
    _build_queries,
    _build_query_subreddit_pairs,
    _build_subreddits,
    _filter_posts,
    _to_research_discoveries,
    _validate_and_dedupe,
    run_reddit_agent,
)
from app.schemas import TripParams
from app.signals import extract_signals
from app.tools.reddit import RedditPost


def _trip(**overrides) -> TripParams:
    base = dict(
        trip_id="t1",
        user_id="u1",
        destination="Manali, India",
        date_from="2026-07-10",
        date_to="2026-07-17",
        duration_days=7,
        travelers="2",
        vibes=["adventure", "hidden gems"],
        accommodation="Hotel",
        pace="Balanced",
        budget="$$",
        preferences=None,
    )
    base.update(overrides)
    return TripParams(**base)


def _post(
    post_id: str,
    *,
    title: str = "A real post",
    body: str = "Some thoughtful body text",
    subreddit: str = "travel",
    score: int = 50,
    comments: list[str] | None = None,
) -> RedditPost:
    return RedditPost(
        post_id=post_id,
        subreddit=subreddit,
        title=title,
        selftext=body,
        score=score,
        num_comments=10,
        permalink=f"/r/{subreddit}/comments/{post_id}/x/",
        url=f"https://reddit.com/r/{subreddit}/comments/{post_id}/x/",
        author="u",
        created_utc=1.0,
        comments=comments or [],
    )


def _ins(
    topic: str = "Manali-Leh highway",
    *,
    insight: str = (
        "The Manali-Leh highway is closed in heavy monsoon — landslides near "
        "Rohtang shut sections through July. Take it after Sept."
    ),
    category: str = "warning",
    evidence: list[int] | None = None,
    tags: list[str] | None = None,
    confidence: str = "high",
) -> _ExtractedInsight:
    return _ExtractedInsight(
        topic=topic,
        insight=insight,
        category=category,  # type: ignore[arg-type]
        evidence_post_indices=evidence if evidence is not None else [1, 2],
        tags=tags or ["road", "monsoon"],
        confidence=confidence,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Subreddit + query construction
# ---------------------------------------------------------------------------


def test_build_subreddits_includes_defaults_and_destination_specific() -> None:
    trip = _trip(destination="Manali, India")
    subs = _build_subreddits(trip)
    assert "travel" in subs and "solotravel" in subs
    # Destination map should add IndiaTravel + ladakh (manali key) + india.
    assert any(s.lower() == "india" for s in subs)
    assert any(s.lower() == "indiatravel" for s in subs)


def test_build_subreddits_unknown_destination_keeps_defaults() -> None:
    trip = _trip(destination="Some Random Town, Antarctica")
    subs = _build_subreddits(trip)
    assert subs == ["travel", "solotravel"]


def test_build_queries_always_includes_tips_and_warnings() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    queries = _build_queries(trip, signals)
    assert any("tips" in q.lower() for q in queries)
    assert any("avoid" in q.lower() for q in queries)
    # Capped.
    assert 4 <= len(queries) <= 6


def test_build_queries_adds_avoid_tourists_for_peak_crowd() -> None:
    """Per spec: high crowd → 'hidden gems avoid tourists' modifier."""
    # Goa + Christmas/NYE → very_peak crowd
    trip = _trip(
        destination="Goa, India",
        date_from="2026-12-26",
        date_to="2027-01-02",
        vibes=["beaches"],
    )
    signals = extract_signals(trip)
    assert signals.crowd_level in {"peak", "very_peak"}
    queries = _build_queries(trip, signals)
    assert any("avoid tourists" in q.lower() for q in queries)


def test_build_queries_uses_plain_hidden_gems_off_peak() -> None:
    # Manali in monsoon = low crowd
    trip = _trip()
    signals = extract_signals(trip)
    assert signals.crowd_level == "low"
    queries = _build_queries(trip, signals)
    assert any(
        "hidden gems" in q.lower() and "avoid tourists" not in q.lower()
        for q in queries
    )


def test_build_queries_includes_first_vibe_and_season() -> None:
    trip = _trip(vibes=["adventure"])
    signals = extract_signals(trip)
    queries = _build_queries(trip, signals)
    assert any(q.endswith("adventure") for q in queries)
    assert any("monsoon" in q for q in queries)


def test_build_query_subreddit_pairs_caps_size() -> None:
    queries = ["q1", "q2", "q3", "q4", "q5", "q6"]
    subs = ["travel", "solotravel", "goa", "india"]
    pairs = _build_query_subreddit_pairs(queries, subs)
    # 6 queries × (2 default + 1 extra) = 18 pairs, capped to MAX_QUERIES*3 = 18
    assert len(pairs) <= 18
    # Each query should appear with both default subs.
    by_q: dict[str, list[str]] = {}
    for q, s in pairs:
        by_q.setdefault(q, []).append(s)
    for q, slist in by_q.items():
        assert "travel" in slist
        assert "solotravel" in slist


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


def test_filter_posts_drops_low_score() -> None:
    posts = [
        _post("a", score=2),  # below floor
        _post("b", score=50),
    ]
    out = _filter_posts(posts)
    assert {p.post_id for p in out} == {"b"}


def test_filter_posts_dedupes_by_title_keeps_highest_score() -> None:
    posts = [
        _post("a", title="Goa Tips", score=10, subreddit="travel"),
        _post("b", title="goa tips", score=80, subreddit="goa"),
    ]
    out = _filter_posts(posts)
    assert len(out) == 1
    assert out[0].post_id == "b"


# ---------------------------------------------------------------------------
# Validation + dedupe
# ---------------------------------------------------------------------------


def test_validate_drops_vague_insights() -> None:
    extracted = [
        _ins(topic="Vague Spot", insight="Be careful when you visit, good vibes overall."),
        _ins(topic="Real Spot"),
    ]
    survivors = _validate_and_dedupe(extracted, n_posts=5)
    assert [d.topic for d in survivors] == ["Real Spot"]


def test_validate_drops_short_insights() -> None:
    extracted = [
        _ins(topic="Tiny", insight="Skip it."),  # < MIN_INSIGHT_LENGTH
        _ins(topic="Real Spot"),
    ]
    survivors = _validate_and_dedupe(extracted, n_posts=5)
    assert [d.topic for d in survivors] == ["Real Spot"]


def test_validate_drops_no_evidence() -> None:
    extracted = [
        _ins(topic="Phantom", evidence=[99]),  # out of range for n=5
        _ins(topic="Real"),
    ]
    survivors = _validate_and_dedupe(extracted, n_posts=5)
    assert [d.topic for d in survivors] == ["Real"]


def test_validate_dedupes_by_topic_keeps_best() -> None:
    extracted = [
        _ins(topic="Manali-Leh highway", evidence=[1], confidence="medium"),
        _ins(topic="manali-leh highway", evidence=[1, 2, 3], confidence="high"),
    ]
    survivors = _validate_and_dedupe(extracted, n_posts=5)
    assert len(survivors) == 1
    assert survivors[0].confidence == "high"


# ---------------------------------------------------------------------------
# Schema coercion (small models often emit malformed JSON shapes)
# ---------------------------------------------------------------------------


def test_extracted_insight_coerces_string_indices() -> None:
    """Groq llama-3.1-8b-instant emits indices as JSON-string '[4]'. Accept it."""
    ins = _ExtractedInsight.model_validate(
        {
            "topic": "Kuari Pass trek",
            "insight": (
                "Welcoming villages at the basecamp invite trekkers in for tea; "
                "Karchi, Tugasi, and Karchon are friendly stops."
            ),
            "category": "recommendation",
            "evidence_post_indices": "[4]",  # string instead of list
            "tags": "trek, basecamp",  # string instead of list
            "confidence": "low",
        }
    )
    assert ins.evidence_post_indices == [4]
    assert ins.tags == ["trek", "basecamp"]


def test_extracted_insight_coerces_string_list_indices() -> None:
    ins = _ExtractedInsight.model_validate(
        {
            "topic": "Real Spot",
            "insight": "x" * 50,
            "category": "tip",
            "evidence_post_indices": ["1", "2", 3],  # mixed
            "tags": ["one"],
            "confidence": "high",
        }
    )
    assert ins.evidence_post_indices == [1, 2, 3]


def test_extracted_insight_coerces_single_int_index() -> None:
    ins = _ExtractedInsight.model_validate(
        {
            "topic": "Real Spot",
            "insight": "x" * 50,
            "category": "tip",
            "evidence_post_indices": 7,  # bare int
            "tags": ["one"],
            "confidence": "high",
        }
    )
    assert ins.evidence_post_indices == [7]


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_to_research_discoveries_prefixes_body_with_category() -> None:
    extracted = [
        _ins(category="warning", topic="Rohtang Pass"),
        _ins(category="recommendation", topic="Vinayak fish thali"),
        _ins(category="tip", topic="Sleeper bus from Delhi"),
    ]
    out = _to_research_discoveries(extracted)
    assert all(d.source == "reddit" for d in out)
    assert out[0].body.startswith("Warning")
    assert out[1].body.startswith("Locals recommend")
    assert out[2].body.startswith("Tip")


# ---------------------------------------------------------------------------
# Full agent flow with mocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_reddit_agent_returns_empty_when_search_returns_nothing() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    with (
        patch(
            "app.agents.reddit.search_many_with_rate_limit",
            AsyncMock(return_value=[]),
        ),
        patch("app.agents.reddit.enrich_with_comments", AsyncMock(return_value=None)),
    ):
        result = await run_reddit_agent(trip, signals)
    assert result == []


@pytest.mark.asyncio
async def test_run_reddit_agent_swallows_unexpected_errors() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    fake_posts = [_post("a"), _post("b")]
    with (
        patch(
            "app.agents.reddit.search_many_with_rate_limit",
            AsyncMock(return_value=fake_posts),
        ),
        patch("app.agents.reddit.enrich_with_comments", AsyncMock(return_value=None)),
        patch(
            "app.agents.reddit._extract_via_llm",
            AsyncMock(side_effect=Exception("LLM blew up")),
        ),
    ):
        result = await run_reddit_agent(trip, signals)
    assert result == []


@pytest.mark.asyncio
async def test_run_reddit_agent_happy_path_with_mocks() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    fake_posts = [
        _post("a", subreddit="IndiaTravel", title="Manali in monsoon"),
        _post("b", subreddit="travel", title="Skip Rohtang in July"),
        _post("c", subreddit="solotravel", title="Local food tips"),
    ]
    extracted = [
        _ExtractedInsight(
            topic="Rohtang Pass road in July",
            insight=(
                "Several redditors reported the Manali-Leh highway closed near "
                "Rohtang due to landslides; convoys delayed by 6+ hours."
            ),
            category="warning",
            evidence_post_indices=[1, 2],
            tags=["road", "monsoon", "manali"],
            confidence="high",
        ),
        _ExtractedInsight(
            topic="Old Manali cafés (Drifters' Inn)",
            insight=(
                "Multiple posters point to Drifters' Inn for filter coffee + "
                "trout, quieter than Mall Road."
            ),
            category="recommendation",
            evidence_post_indices=[3],
            tags=["cafe", "old-manali"],
            confidence="medium",
        ),
    ]
    with (
        patch(
            "app.agents.reddit.search_many_with_rate_limit",
            AsyncMock(return_value=fake_posts),
        ),
        patch("app.agents.reddit.enrich_with_comments", AsyncMock(return_value=None)),
        patch(
            "app.agents.reddit._extract_via_llm",
            AsyncMock(return_value=extracted),
        ),
    ):
        result = await run_reddit_agent(trip, signals)

    assert len(result) == 2
    assert all(d.source == "reddit" for d in result)
    titles = {d.title for d in result}
    assert "Rohtang Pass road in July" in titles
    # Warning prefix should be applied.
    warn = next(d for d in result if "Rohtang" in d.title)
    assert warn.body.startswith("Warning")


@pytest.mark.asyncio
async def test_run_reddit_agent_drops_vague_llm_output() -> None:
    trip = _trip()
    signals = extract_signals(trip)
    fake_posts = [_post("a")]
    extracted = [
        _ExtractedInsight(
            topic="Manali",
            insight="It has vibrant culture and you must-visit it.",
            category="recommendation",
            evidence_post_indices=[1],
            tags=["culture"],
            confidence="high",
        )
    ]
    with (
        patch(
            "app.agents.reddit.search_many_with_rate_limit",
            AsyncMock(return_value=fake_posts),
        ),
        patch("app.agents.reddit.enrich_with_comments", AsyncMock(return_value=None)),
        patch(
            "app.agents.reddit._extract_via_llm",
            AsyncMock(return_value=extracted),
        ),
    ):
        result = await run_reddit_agent(trip, signals)
    assert result == []
