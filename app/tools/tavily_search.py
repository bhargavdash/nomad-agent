"""Tavily Search tool — travel blog content for a destination.

Tavily returns AI-cleaned text excerpts from web search results. We use it to
fetch travel blog content, filtering out commercial booking/review sites and
sources already covered by the reddit/youtube agents.

API: tavily-python AsyncTavilyClient. Free tier: 1000 searches/month.
Requires TAVILY_API_KEY env var.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

# Commercial / non-editorial sites excluded from every query. We want curated
# travel writing, not hotel-booking copy or content the other agents cover.
_EXCLUDED_DOMAINS = [
    "tripadvisor.com",
    "booking.com",
    "hotels.com",
    "expedia.com",
    "airbnb.com",
    "agoda.com",
    "makemytrip.com",
    "cleartrip.com",
    "ixigo.com",
    "goibibo.com",
    "reddit.com",   # reddit agent's territory
    "youtube.com",  # youtube agent's territory
    "tiktok.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "pinterest.com",
    "yelp.com",
    "foursquare.com",
]

MAX_RESULTS_PER_QUERY = 5

# Freshness window for blog content. 2 years drops most "best of 2018" lists
# (where featured restaurants/hotels often no longer exist) while keeping
# evergreen guide content like history and architecture references.
TAVILY_RECENCY_DAYS = 730


@dataclass
class TavilyResult:
    """One article returned by Tavily."""

    title: str
    url: str
    content: str   # AI-cleaned excerpt, typically 200–500 chars
    score: float   # Tavily relevance score 0–1


async def search_travel_blogs(
    query: str,
    *,
    max_results: int = MAX_RESULTS_PER_QUERY,
) -> list[TavilyResult]:
    """Search for travel blog articles matching `query`.

    Returns [] if TAVILY_API_KEY is not set or on any error — graceful
    degradation matches the rest of the pipeline contract.
    """
    if not settings.TAVILY_API_KEY:
        logger.warning("tavily.search skipped: TAVILY_API_KEY not set")
        return []
    try:
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
        response = await client.search(
            query=query,
            search_depth="basic",
            max_results=min(max_results, 10),
            exclude_domains=_EXCLUDED_DOMAINS,
            include_answer=False,
            days=TAVILY_RECENCY_DAYS,
        )
        raw_results = response.get("results", []) if isinstance(response, dict) else []
        out: list[TavilyResult] = []
        for r in raw_results:
            if not isinstance(r, dict):
                continue
            content = (r.get("content") or "").strip()
            if not content:
                continue
            out.append(
                TavilyResult(
                    title=(r.get("title") or "").strip(),
                    url=(r.get("url") or "").strip(),
                    content=content,
                    score=float(r.get("score") or 0.0),
                )
            )
        logger.info("tavily.search q=%r results=%d", query, len(out))
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("tavily.search failed q=%r err=%s", query, e)
        return []


async def search_fanout(
    queries: list[str],
    *,
    max_results_per_query: int = MAX_RESULTS_PER_QUERY,
) -> list[TavilyResult]:
    """Run multiple Tavily queries in parallel; dedupe by URL keeping highest score.

    Unlike the Reddit tool, Tavily has no strict per-second rate limit on the
    free tier so we fan out all queries concurrently. Falls back gracefully
    per-query on errors.
    """
    if not queries:
        return []
    results_per_query = await asyncio.gather(
        *(
            search_travel_blogs(q, max_results=max_results_per_query)
            for q in queries
        ),
        return_exceptions=True,
    )
    by_url: dict[str, TavilyResult] = {}
    for q, res in zip(queries, results_per_query):
        if isinstance(res, BaseException):
            logger.warning("tavily.fanout_failed q=%r err=%s", q, res)
            continue
        for r in res:
            if not r.url:
                continue
            existing = by_url.get(r.url)
            if existing is None or r.score > existing.score:
                by_url[r.url] = r
    deduped = sorted(by_url.values(), key=lambda r: r.score, reverse=True)
    logger.info(
        "tavily.fanout queries=%d unique_results=%d", len(queries), len(deduped)
    )
    return deduped
