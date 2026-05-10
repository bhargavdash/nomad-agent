"""YouTubeShortsAgent — extract authentic place mentions from <60s Shorts.

Pipeline:
  1. Build query from destination + signals.query_modifiers (top N).
  2. search_youtube_shorts() → list of YouTubeShort (already filtered to <60s).
  3. Apply quality filters: dedupe channel, drop very-low view counts.
  4. Build a structured prompt listing all Short titles + descriptions.
  5. LLM extracts ResearchDiscovery objects with cross-Short aggregation
     ("place mentioned in 3+ Shorts → confidence: high").
  6. Return list[ResearchDiscovery] tagged source="youtube".

Failure modes (all return [] gracefully — synthesizer continues with other agents):
  - YOUTUBE_API_KEY missing
  - API quota exceeded (HTTP 403)
  - Search returns 0 Shorts (rare destinations, very recent)
  - LLM call fails or returns invalid JSON
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm.factory import get_llm
from app.schemas import ResearchDiscovery, TripParams
from app.signals import TravelSignals
from app.tools.youtube import YouTubeShort, search_youtube_shorts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_SHORTS_PER_QUERY = 25
MAX_QUERY_MODIFIERS = 4   # don't over-constrain the search query
MIN_VIEW_COUNT = 500      # drop near-zero-view Shorts (likely spam/bot)
MAX_DISCOVERIES_RETURNED = 8


# ---------------------------------------------------------------------------
# LLM output schema (forces structured extraction)
# ---------------------------------------------------------------------------


class _ExtractedDiscovery(BaseModel):
    """One discovery extracted by the LLM. Maps to ResearchDiscovery."""

    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=20, max_length=600)
    tags: list[str] = Field(..., min_length=1, max_length=3)
    confidence: str = Field(
        ..., description="'high' | 'medium' | 'low' — high if mentioned in 3+ Shorts"
    )


class _ExtractionResult(BaseModel):
    discoveries: list[_ExtractedDiscovery]


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _build_query(trip_params: TripParams, signals: TravelSignals) -> str:
    """Combine destination + a few of the most relevant signal modifiers.

    Examples:
      Goa, India + ['avoid crowds', 'Christmas/NYE celebrations', 'nightlife']
        → 'Goa India nightlife Christmas/NYE travel shorts'
      Manali, India + ['monsoon', 'indoor activities', 'adventure']
        → 'Manali India monsoon adventure travel shorts'
    """
    parts: list[str] = [trip_params.destination]

    # Pick the most "search-friendly" modifiers (skip things like "off-the-beaten-path"
    # that are noise inside a YouTube query).
    skip_substrings = (
        "avoid",
        "off-the-beaten",
        "early morning",
        "best weather",
        "less crowded",
        "local favorites",
    )
    picked = 0
    for mod in signals.query_modifiers:
        if picked >= MAX_QUERY_MODIFIERS:
            break
        if any(s in mod.lower() for s in skip_substrings):
            continue
        parts.append(mod)
        picked += 1

    parts.append("travel shorts")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------


def _filter_quality(shorts: list[YouTubeShort]) -> list[YouTubeShort]:
    """Drop very-low-view items, dedupe by channel (keep best) so a single
    creator can't dominate the results."""
    filtered = [s for s in shorts if s.view_count >= MIN_VIEW_COUNT]

    # Keep best Short per channel by view count (proxy for quality).
    by_channel: dict[str, YouTubeShort] = {}
    for s in filtered:
        existing = by_channel.get(s.channel_title)
        if existing is None or s.view_count > existing.view_count:
            by_channel[s.channel_title] = s

    deduped = sorted(by_channel.values(), key=lambda s: s.view_count, reverse=True)
    return deduped[:MAX_SHORTS_PER_QUERY]


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You extract concrete travel insights from short-form YouTube videos (<60s Shorts).

Your inputs are titles + descriptions from real travelers' Shorts about a specific destination.
Your job: identify SPECIFIC places, food, experiences, or warnings that would help someone
plan a trip there.

CRITICAL RULES:
- Extract concrete things only: specific restaurants, beaches, viewpoints, dishes, tips.
  NEVER output generic items like "Local Museum" or "Famous Beach".
- If a place/experience is mentioned across MULTIPLE Shorts (different creators),
  set confidence="high" and say so in the body ("mentioned across 3 different creators").
- If only 1 Short mentions something but it sounds genuine and specific, confidence="medium".
- If you're unsure or the source is thin, confidence="low" — but prefer to drop the item.
- Tags: 1-3 short keywords (e.g. ["beach", "sunset", "north-goa"]).
- Body: 2-4 sentences. Include the place name early. Mention what makes it worth visiting.
- Title: a punchy headline for the discovery (max 80 chars).

OUTPUT FORMAT: a JSON object with shape {"discoveries": [...]}. Each discovery has fields:
  title, body, tags, confidence.

Return at most 8 discoveries. Quality > quantity. Skip generic content."""


def _format_shorts_for_prompt(shorts: list[YouTubeShort]) -> str:
    """Render the Shorts as a readable list the LLM can scan."""
    lines: list[str] = []
    for i, s in enumerate(shorts, 1):
        desc = (s.description or "").strip().replace("\n", " ")
        if len(desc) > 400:
            desc = desc[:400] + "..."
        lines.append(
            f"[{i}] @{s.channel_title} — {s.view_count:,} views\n"
            f"    Title: {s.title}\n"
            f"    Description: {desc}"
        )
    return "\n\n".join(lines)


def _build_user_prompt(
    trip_params: TripParams, signals: TravelSignals, shorts: list[YouTubeShort]
) -> str:
    festival_line = (
        f"Active festivals during trip: {', '.join(signals.active_festivals)}\n"
        if signals.active_festivals
        else ""
    )
    return (
        f"Destination: {trip_params.destination}\n"
        f"Trip dates: {trip_params.date_from} to {trip_params.date_to}\n"
        f"Season: {signals.season} (crowd level: {signals.crowd_level})\n"
        f"{festival_line}"
        f"Vibes: {', '.join(trip_params.vibes) if trip_params.vibes else '—'}\n"
        f"Budget tier: {signals.budget_tier}\n\n"
        f"YouTube Shorts (<60s) about this destination:\n\n"
        f"{_format_shorts_for_prompt(shorts)}\n\n"
        f"Extract concrete travel discoveries from these Shorts. "
        f"Pay special attention to places mentioned across multiple Shorts."
    )


# ---------------------------------------------------------------------------
# LLM call + parsing
# ---------------------------------------------------------------------------


async def _extract_via_llm(
    trip_params: TripParams, signals: TravelSignals, shorts: list[YouTubeShort]
) -> list[_ExtractedDiscovery]:
    llm = get_llm("youtube_agent")
    structured = llm.with_structured_output(_ExtractionResult)

    messages: list[Any] = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=_build_user_prompt(trip_params, signals, shorts)),
    ]
    result = await structured.ainvoke(messages)
    if not isinstance(result, _ExtractionResult):
        # langchain may return dict on some providers — coerce
        result = _ExtractionResult.model_validate(result)
    return result.discoveries[:MAX_DISCOVERIES_RETURNED]


def _to_research_discoveries(
    extracted: list[_ExtractedDiscovery],
) -> list[ResearchDiscovery]:
    out: list[ResearchDiscovery] = []
    for d in extracted:
        # Tags must be <=3 per ResearchDiscovery schema; trim defensively.
        tags = [t for t in d.tags if t][:3] or ["youtube"]
        out.append(
            ResearchDiscovery(
                id=str(uuid.uuid4()),
                title=d.title.strip(),
                body=d.body.strip(),
                tags=tags,
                source="youtube",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_youtube_agent(
    trip_params: TripParams, signals: TravelSignals
) -> list[ResearchDiscovery]:
    """Return YouTube-Shorts-derived discoveries for the trip.

    All errors are caught and result in []; the synthesizer continues with
    whatever the other agents found.
    """
    try:
        query = _build_query(trip_params, signals)
        logger.info("youtube_agent.start query=%r", query)

        shorts = await search_youtube_shorts(query, max_results=MAX_SHORTS_PER_QUERY)
        if not shorts:
            logger.warning("youtube_agent: 0 Shorts returned for %r", query)
            return []

        filtered = _filter_quality(shorts)
        logger.info(
            "youtube_agent: %d Shorts after quality filter (from %d)",
            len(filtered),
            len(shorts),
        )
        if not filtered:
            return []

        extracted = await _extract_via_llm(trip_params, signals, filtered)
        discoveries = _to_research_discoveries(extracted)
        logger.info("youtube_agent.done returning %d discoveries", len(discoveries))
        return discoveries

    except RuntimeError as e:
        # Missing API key, etc. — log and return [] for graceful degradation
        logger.error("youtube_agent config error: %s", e)
        return []
    except Exception as e:  # noqa: BLE001
        logger.exception("youtube_agent unexpected failure: %s", e)
        return []
