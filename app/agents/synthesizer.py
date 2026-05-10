"""SynthesizerAgent — STUB.

Strategy (from AI_INTEGRATION_PLAN.md §4.4):
  • Pure LLM reasoning over collected discoveries — no external tool calls.
  • Receives trip_params, signals, and ALL discoveries from research agents.
  • Plans the day-by-day shape (multi-city splits if applicable).
  • Slots discoveries into days based on:
      - Signal matching (outdoor in clear weather, indoor in monsoon).
      - signals.vibe_source_weights (which agent's findings dominate).
      - signals.pace_density (3 / 4 / 5 stops per day).
  • Fills gaps with sensible standard anchors (museum, viewpoint),
    tagged source="maps".
  • Generates day titles, descriptions, highlights, stop times/durations,
    per-stop emoji `tags` arrays.
  • Output: AIItinerary matching the Pydantic schema.
  • Hard rule in prompt: every stop MUST reference a discovery, OR be
    explicitly tagged source="maps" as a "standard anchor".
  • Default LLM: Anthropic Claude Sonnet 4.6 (flagship — quality matters here).
"""

from __future__ import annotations

from app.schemas import AIDay, AIItinerary, AIStop, ResearchDiscovery, TripParams
from app.signals import TravelSignals


def _placeholder_itinerary(trip_params: TripParams) -> AIItinerary:
    """Hard-coded sample itinerary so the pipeline can run end-to-end."""
    sample_stops = [
        AIStop(
            sortOrder=1,
            time="9:00",
            ampm="AM",
            duration="1h",
            name="Placeholder breakfast spot",
            description="Stub stop — synthesizer not yet implemented.",
            source="maps",
            tags=["☕"],
        ),
        AIStop(
            sortOrder=2,
            time="11:00",
            ampm="AM",
            duration="2h",
            name="Placeholder anchor",
            description="Stub stop — synthesizer not yet implemented.",
            source="maps",
            tags=["📍"],
        ),
        AIStop(
            sortOrder=3,
            time="2:00",
            ampm="PM",
            duration="2h",
            name="Placeholder evening activity",
            description="Stub stop — synthesizer not yet implemented.",
            source="maps",
            tags=["🌅"],
        ),
    ]
    sample_day = AIDay(
        dayNumber=1,
        city=trip_params.destination,
        title=f"Day 1 in {trip_params.destination}",
        description="Stub day — synthesizer not yet implemented.",
        highlights=["Placeholder highlight 1", "Placeholder highlight 2"],
        stops=sample_stops,
    )
    sample_discoveries = [
        ResearchDiscovery(
            id=f"stub-{i}",
            title=f"Stub discovery {i}",
            body="Placeholder body until real agents are wired in.",
            tags=["stub"],
            source="maps",
        )
        for i in range(1, 4)
    ]
    return AIItinerary(
        emoji="🧭",
        stats_places=3,
        stats_tips=0,
        stats_photo_stops=1,
        discoveries=sample_discoveries,
        days=[sample_day],
    )


async def run_synthesizer(
    trip_params: TripParams,
    signals: TravelSignals,
    discoveries: list[ResearchDiscovery],
) -> AIItinerary:
    """Compose the final itinerary from all discoveries.

    STUB: returns a hardcoded placeholder AIItinerary so the pipeline
    can run end-to-end. Real LLM-driven synthesis lands in Sprint 3.
    """
    # TODO: implement
    #   1. Group discoveries by source; apply signals.vibe_source_weights.
    #   2. Plan day shape: multi-city split based on duration_days.
    #   3. Build the heavy synthesizer prompt (plan §4.4) with the
    #      "every stop ↔ discovery OR source=maps" hard rule.
    #   4. call get_llm("synthesizer"), parse JSON.
    #   5. Validate against AIItinerary; retry once on validation failure.
    return _placeholder_itinerary(trip_params)
