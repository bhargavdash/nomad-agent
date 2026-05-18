"""Unit tests for the Reddit JSON API tool layer.

No real network — uses httpx.MockTransport.
"""

from __future__ import annotations

import httpx
import pytest

from app.tools import reddit as reddit_tool
from app.tools.reddit import (
    RedditBlockedError,
    RedditPost,
    _parse_listing,
    _parse_top_comments,
    fetch_top_comments,
    search_reddit,
)


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------


def test_parse_listing_extracts_required_fields() -> None:
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "abc123",
                        "subreddit": "goa",
                        "title": "Tips for monsoon Goa",
                        "selftext": "Roads can flood, beaches closed.",
                        "score": 42,
                        "num_comments": 17,
                        "permalink": "/r/goa/comments/abc123/tips/",
                        "url": "https://reddit.com/r/goa/comments/abc123/tips/",
                        "author": "wanderer",
                        "created_utc": 1700000000.0,
                    }
                },
                {
                    # Missing id — should be skipped.
                    "data": {"title": "no id", "selftext": ""}
                },
                {
                    # Missing title — should be skipped.
                    "data": {"id": "xyz", "selftext": ""}
                },
            ]
        }
    }
    posts = _parse_listing(payload)
    assert len(posts) == 1
    p = posts[0]
    assert p.post_id == "abc123"
    assert p.subreddit == "goa"
    assert p.score == 42
    assert "flood" in p.selftext


def test_parse_listing_handles_garbage() -> None:
    assert _parse_listing({}) == []
    assert _parse_listing([]) == []  # type: ignore[arg-type]
    assert _parse_listing({"data": {}}) == []


def test_parse_top_comments_skips_stickied_and_deleted() -> None:
    payload = [
        {"_post": "ignored"},
        {
            "data": {
                "children": [
                    {"kind": "t1", "data": {"body": "sticky", "stickied": True}},
                    {"kind": "t1", "data": {"body": "[deleted]"}},
                    {"kind": "t1", "data": {"body": "[removed]"}},
                    {
                        "kind": "t1",
                        "data": {
                            "body": "skip Baga, locals go to Cola Beach in monsoon",
                        },
                    },
                    {
                        "kind": "t1",
                        "data": {"body": "Watch out for the road near Solang."},
                    },
                    {"kind": "more", "data": {"body": "load more"}},  # wrong kind
                    {"kind": "t1", "data": {"body": "Vinayak fish thali ftw."}},
                ]
            }
        },
    ]
    out = _parse_top_comments(payload, max_comments=3)
    assert len(out) == 3
    assert "Cola Beach" in out[0]
    assert "Solang" in out[1]
    assert "Vinayak" in out[2]


def test_parse_top_comments_handles_garbage() -> None:
    assert _parse_top_comments([], 3) == []
    assert _parse_top_comments({"not": "a list"}, 3) == []  # type: ignore[arg-type]
    assert _parse_top_comments([{}], 3) == []


# ---------------------------------------------------------------------------
# Mocked end-to-end
# ---------------------------------------------------------------------------


def _patch_async_client(monkeypatch, transport: httpx.MockTransport) -> None:
    real_async_client = httpx.AsyncClient

    def patched(*args, **kwargs):  # noqa: ANN001
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(reddit_tool.httpx, "AsyncClient", patched)


@pytest.mark.asyncio
async def test_search_reddit_hits_correct_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["user_agent"] = request.headers.get("User-Agent")
        return httpx.Response(
            200,
            json={
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "p1",
                                "subreddit": "goa",
                                "title": "Goa monsoon tips",
                                "selftext": "Avoid Baga in July.",
                                "score": 25,
                                "num_comments": 8,
                                "permalink": "/r/goa/comments/p1/x/",
                                "url": "https://reddit.com/x",
                                "author": "u",
                                "created_utc": 1735689600.0,  # 2025-01-01, fresh enough for the age filter
                            }
                        }
                    ]
                }
            },
        )

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))

    posts = await search_reddit("monsoon tips", "goa", limit=5)
    assert len(posts) == 1
    assert posts[0].post_id == "p1"
    assert isinstance(posts[0], RedditPost)
    # Endpoint + params shape:
    assert captured["path"] == "/r/goa/search.json"
    assert captured["params"]["q"] == "monsoon tips"
    assert captured["params"]["restrict_sr"] == "1"
    assert captured["params"]["sort"] == "relevance"
    assert captured["params"]["t"] == "year"
    assert captured["params"]["limit"] == "5"
    # Custom UA must be sent — Reddit blocks defaults / crawler-flagged strings.
    ua = str(captured["user_agent"] or "")
    assert ua and "nomad-agent" in ua
    # Must NOT contain words Reddit's anti-abuse layer flags.
    assert not any(bad in ua.lower() for bad in ("crawler", "scraper", "bot"))


@pytest.mark.asyncio
async def test_search_reddit_raises_blocked_on_403(monkeypatch) -> None:
    """403 on both www and old.reddit.com → RedditBlockedError so the caller
    can short-circuit fan-out instead of grinding through doomed requests."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "blocked"})

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(RedditBlockedError):
        await search_reddit("anything", "travel")


@pytest.mark.asyncio
async def test_search_reddit_falls_back_to_old_reddit_on_403(monkeypatch) -> None:
    """If www.reddit.com 403s but old.reddit.com responds, we should succeed."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "old.reddit.com":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "children": [
                            {
                                "data": {
                                    "id": "p1",
                                    "subreddit": "travel",
                                    "title": "Old reddit worked",
                                    "selftext": "",
                                    "score": 5,
                                    "num_comments": 1,
                                    "permalink": "/r/travel/comments/p1/x/",
                                    "url": "https://reddit.com/x",
                                    "author": "u",
                                    "created_utc": 1735689600.0,  # 2025-01-01, fresh enough for the age filter
                                }
                            }
                        ]
                    }
                },
            )
        return httpx.Response(403, json={"error": "blocked"})

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))
    posts = await search_reddit("anything", "travel")
    assert len(posts) == 1
    assert posts[0].post_id == "p1"


@pytest.mark.asyncio
async def test_search_reddit_returns_empty_on_5xx(monkeypatch) -> None:
    """Server errors are treated as transient — return [] without fallback."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))
    posts = await search_reddit("anything", "travel")
    assert posts == []


@pytest.mark.asyncio
async def test_fetch_top_comments_returns_empty_on_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))
    out = await fetch_top_comments("/r/x/comments/abc/", max_comments=3)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_top_comments_returns_empty_for_blank_permalink() -> None:
    assert await fetch_top_comments("", max_comments=3) == []


# ---------------------------------------------------------------------------
# Fan-out circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_many_aborts_after_consecutive_403s(monkeypatch) -> None:
    """When Reddit blocks us, keep going only depthens the block. Verify the
    fan-out gives up after a streak of 403s instead of grinding through the
    full list of (query, sub) pairs."""
    from app.tools import reddit as reddit_tool

    call_count = 0

    async def fake_search_reddit(*args, **kwargs):  # noqa: ANN001, ANN002
        nonlocal call_count
        call_count += 1
        raise reddit_tool.RedditBlockedError("blocked")

    monkeypatch.setattr(reddit_tool, "search_reddit", fake_search_reddit)

    queries = [(f"q{i}", "travel") for i in range(10)]
    out = await reddit_tool.search_many_with_rate_limit(
        queries, limit_per_query=5, sleep_seconds=0
    )
    assert out == []
    # Should give up after 3 consecutive blocks, not run all 10.
    assert call_count == 3
