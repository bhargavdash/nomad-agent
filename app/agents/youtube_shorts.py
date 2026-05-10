"""YouTubeShortsAgent — STUB.

Strategy (from AI_INTEGRATION_PLAN.md §4.1):
  • Build query from destination + signals.query_modifiers + vibe keywords.
  • search.list with videoDuration=short → top 25 results.
  • videos.list → contentDetails.duration (ISO 8601). Filter to <60s Shorts.
  • Optionally fetch auto-captions via youtube-transcript-api.
  • Aggregate: place names appearing in 3+ Shorts get a "trending" boost.
  • Quality multipliers: channel ≥ 1k subs, recency boost, view velocity,
    repeat-mention dedup.
  • LLM extraction → ResearchDiscovery[] tagged source="youtube".
  • Default LLM: Groq Llama 3.3 70B (cheap, fast — fine for metadata).
"""

from __future__ import annotations

from app.schemas import ResearchDiscovery, TripParams
from app.signals import TravelSignals


async def run_youtube_agent(
    trip_params: TripParams, signals: TravelSignals
) -> list[ResearchDiscovery]:
    """Return YouTube-Shorts-derived discoveries for the trip.

    STUB: returns []. Real implementation lands in Sprint 2.
    """
    # TODO: implement
    #   1. Build query string from trip_params.destination + signals.query_modifiers.
    #   2. Call YouTube Data API v3 search.list (videoDuration=short, maxResults=25).
    #   3. Fetch contentDetails for results, filter to duration < 60s.
    #   4. Apply channel/recency/view-velocity quality filters.
    #   5. Fetch auto-captions where available via youtube-transcript-api.
    #   6. Aggregate place mentions across Shorts; boost repeat mentions.
    #   7. Build prompt (see plan §4.1), call get_llm("youtube_agent").
    #   8. Parse JSON, validate against ResearchDiscovery, return list.
    return []
