"""GoogleBlogAgent — STUB.

Strategy (from AI_INTEGRATION_PLAN.md §4.3):
  • Tool: Tavily Search API (free 1000 queries/month, AI-ready summaries).
  • Query: "best places ${destination} ${season} ${vibes}" with
    -tripadvisor.com -reddit.com to avoid double-covering sources.
  • Tavily returns top 5 articles with summaries.
  • Extract recommendations via LLM. Tag "blog" unless specifically a
    Google-Maps top-rated tourist anchor → "maps".
  • Travel blogs are best at curated lists w/ reasoning, historical/
    cultural context, logistics & itinerary suggestions.
"""

from __future__ import annotations

from app.schemas import ResearchDiscovery, TripParams
from app.signals import TravelSignals


async def run_google_blog_agent(
    trip_params: TripParams, signals: TravelSignals
) -> list[ResearchDiscovery]:
    """Return blog-derived discoveries for the trip.

    STUB: returns []. Real implementation lands in Sprint 3.
    """
    # TODO: implement
    #   1. Build query: destination + season + vibe keywords + exclusions.
    #   2. Call Tavily search (5 results, include summaries).
    #   3. Build prompt (plan §4.3), call get_llm("google_agent").
    #   4. Parse JSON, validate, return ResearchDiscovery[] (source="blog"|"maps").
    return []
