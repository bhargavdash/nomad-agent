"""RedditAgent — STUB.

Strategy (from AI_INTEGRATION_PLAN.md §4.2):
  • Identify candidate subreddits: r/travel, r/{destination},
    r/{destination_city}, r/solotravel, r/backpacking.
  • For each: hit /r/{sub}/search.json with destination + tips/gems keywords,
    sort=top, t=year, limit=15.
  • Top 5–10 posts → fetch /comments/{id}.json → grab top 10 comments.
  • Concatenate post body + top comments → LLM extraction.
  • Custom User-Agent header is NON-NEGOTIABLE (Reddit blocks defaults).
  • Look for contrarian patterns: "skip X, go Y", scam warnings, locals
    correcting outdated guidebook recs, specific neighborhoods/hours.
  • Default LLM: Groq Llama; switch to Anthropic if extraction quality drops.
"""

from __future__ import annotations

from app.schemas import ResearchDiscovery, TripParams
from app.signals import TravelSignals


async def run_reddit_agent(
    trip_params: TripParams, signals: TravelSignals
) -> list[ResearchDiscovery]:
    """Return Reddit-derived discoveries for the trip.

    STUB: returns []. Real implementation lands in Sprint 3.
    """
    # TODO: implement
    #   1. Resolve candidate subreddits from destination.
    #   2. For each sub, search posts (sort=top, t=year). Use custom User-Agent.
    #   3. For top posts, fetch comments (top 10).
    #   4. Concatenate threads into prompt context.
    #   5. Build prompt (plan §4.2), call get_llm("reddit_agent").
    #   6. Parse JSON, validate, return ResearchDiscovery[] tagged source="reddit".
    return []
