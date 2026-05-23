"""LangGraph pipeline.

Topology:
    [signal_extractor]  (entry — pure Python, derives TravelSignals)
        │
        ├──► youtube_shorts   ─┐
        ├──► youtube_longform  ─┤
        ├──► reddit            ─┼──► merge ──► synthesizer ──► END
        └──► google_blog       ─┘

The 4 research agents run in parallel (LangGraph fan-out from `signals`);
merge fans them back in and concatenates discoveries; the synthesizer
produces the final itinerary.

Why a separate long-form node (not extending Shorts):
  Long-form vlogs are a different *substrate* (mandatory transcripts), a
  different *failure mode* (listicle/SEO dominance), and a different *cost
  shape* (smaller Pass-1 batches). Two nodes is cleaner than one branching
  agent. Both write to distinct state keys so LangGraph's parallel merge
  doesn't need a custom reducer.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.google_blog import run_google_blog_agent
from app.agents.reddit import run_reddit_agent
from app.agents.synthesizer import run_synthesizer
from app.agents.youtube_longform import run_youtube_longform_agent
from app.agents.youtube_shorts import run_youtube_agent
from app.db import supabase_writer
from app.schemas import AIItinerary, ResearchDiscovery, TripParams
from app.signals import TravelSignals, enrich_anchor_hints, enrich_signals_with_llm, extract_signals

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    trip_params: TripParams
    signals: TravelSignals
    yt_discoveries: list[ResearchDiscovery]
    yt_longform_discoveries: list[ResearchDiscovery]
    reddit_discoveries: list[ResearchDiscovery]
    google_discoveries: list[ResearchDiscovery]
    all_discoveries: list[ResearchDiscovery]
    itinerary: AIItinerary
    error: str | None


# --- Node functions ---------------------------------------------------------


async def signal_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    logger.info("[NODE] signals → starting")
    signals = extract_signals(state["trip_params"])
    signals = await enrich_signals_with_llm(signals, state["trip_params"])
    await enrich_anchor_hints(signals, state["trip_params"].destination)
    logger.info(
        "[NODE] signals → done  region=%s season=%s crowd=%s budget=%s anchors=%d  (%.1fs)",
        signals.region,
        signals.season,
        signals.crowd_level,
        signals.budget_tier,
        len(signals.top_anchors or []),
        time.perf_counter() - t0,
    )
    return {"signals": signals}


async def youtube_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    logger.info("[NODE] youtube_shorts → starting")
    discoveries = await run_youtube_agent(state["trip_params"], state["signals"])
    logger.info("[NODE] youtube_shorts → done  discoveries=%d  (%.1fs)", len(discoveries), time.perf_counter() - t0)
    return {"yt_discoveries": discoveries}


async def youtube_longform_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    logger.info("[NODE] youtube_longform → starting")
    discoveries = await run_youtube_longform_agent(
        state["trip_params"], state["signals"]
    )
    logger.info("[NODE] youtube_longform → done  discoveries=%d  (%.1fs)", len(discoveries), time.perf_counter() - t0)
    return {"yt_longform_discoveries": discoveries}


async def reddit_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    logger.info("[NODE] reddit → starting")
    discoveries = await run_reddit_agent(state["trip_params"], state["signals"])
    logger.info("[NODE] reddit → done  discoveries=%d  (%.1fs)", len(discoveries), time.perf_counter() - t0)
    return {"reddit_discoveries": discoveries}


async def google_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    logger.info("[NODE] google_blog → starting")
    discoveries = await run_google_blog_agent(state["trip_params"], state["signals"])
    logger.info("[NODE] google_blog → done  discoveries=%d  (%.1fs)", len(discoveries), time.perf_counter() - t0)
    return {"google_discoveries": discoveries}


async def merge_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    yt = state.get("yt_discoveries", []) or []
    ytl = state.get("yt_longform_discoveries", []) or []
    red = state.get("reddit_discoveries", []) or []
    goog = state.get("google_discoveries", []) or []
    logger.info(
        "[NODE] merge → yt_shorts=%d  yt_longform=%d  reddit=%d  google=%d  total_in=%d",
        len(yt), len(ytl), len(red), len(goog), len(yt) + len(ytl) + len(red) + len(goog),
    )
    merged: list[ResearchDiscovery] = []
    merged.extend(yt)
    merged.extend(ytl)
    merged.extend(red)
    merged.extend(goog)

    # Pre-seed canonical anchor stops to bypass the extraction LLM's vibe bias.
    # Only add seeds for anchors not already covered by real research (fuzzy match).
    existing_lower = {d.title.lower() for d in merged}
    destination = state["trip_params"].destination
    anchor_seeds: list[ResearchDiscovery] = []
    for name in (state["signals"].top_anchors or []):
        name_lower = name.lower()
        already_covered = any(
            name_lower in existing or existing in name_lower
            for existing in existing_lower
        )
        if not already_covered:
            anchor_seeds.append(
                ResearchDiscovery(
                    id=str(uuid.uuid4()),
                    title=name,
                    body=(
                        f"{name} — a must-visit landmark in {destination}. "
                        "Pre-validated anchor stop. Check local advisories for opening hours."
                    ),
                    source="maps",
                    tags=["anchor_hint"],
                )
            )

    all_discoveries = anchor_seeds + merged

    # Mid-flight write so the FE "LIVE DISCOVERY" card animates at least
    # once before the synthesizer completes. The final full-list write
    # lands from _run_and_persist after the pipeline returns — see
    # FRONTEND_INTEGRATION_PLAN.md §8 Phase 2 (Discoveries streaming).
    # First chunk = first ~half of the discoveries (min 1, max 5) so the
    # FE always sees the array grow on the final write.
    if all_discoveries:
        chunk_size = max(1, min(5, len(all_discoveries) // 2))
        first_chunk = all_discoveries[:chunk_size]
        try:
            await supabase_writer.write_discoveries(
                state["trip_params"].trip_id, first_chunk
            )
        except Exception:  # noqa: BLE001
            # Discoveries are a polish — never fail the pipeline over a
            # write error here. Final write in _run_and_persist still tries.
            logger.warning(
                "merge_node: mid-flight write_discoveries failed for trip %s",
                state["trip_params"].trip_id,
                exc_info=True,
            )

    logger.info(
        "[NODE] merge → done  anchors_seeded=%d  all_discoveries=%d  (%.1fs)",
        len(anchor_seeds),
        len(all_discoveries),
        time.perf_counter() - t0,
    )
    return {"all_discoveries": all_discoveries}


async def synthesizer_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    all_disc = state.get("all_discoveries", [])
    logger.info("[NODE] synthesizer → starting  discoveries=%d", len(all_disc))
    itinerary = await run_synthesizer(
        state["trip_params"],
        state["signals"],
        all_disc,
    )
    logger.info(
        "[NODE] synthesizer → done  days=%d  places=%d  tips=%d  photo=%d  (%.1fs)",
        len(itinerary.days),
        itinerary.stats_places,
        itinerary.stats_tips,
        itinerary.stats_photo_stops,
        time.perf_counter() - t0,
    )
    return {"itinerary": itinerary}


# --- Graph construction -----------------------------------------------------


def build_graph():
    g = StateGraph(PipelineState)

    g.add_node("signals", signal_node)
    g.add_node("youtube", youtube_node)
    g.add_node("youtube_longform", youtube_longform_node)
    g.add_node("reddit", reddit_node)
    g.add_node("google", google_node)
    g.add_node("merge", merge_node)
    g.add_node("synthesizer", synthesizer_node)

    g.add_edge(START, "signals")

    # Fan-out from signals to the 4 research agents in parallel.
    g.add_edge("signals", "youtube")
    g.add_edge("signals", "youtube_longform")
    g.add_edge("signals", "reddit")
    g.add_edge("signals", "google")

    # Fan-in to merge — LangGraph waits for all incoming edges.
    g.add_edge("youtube", "merge")
    g.add_edge("youtube_longform", "merge")
    g.add_edge("reddit", "merge")
    g.add_edge("google", "merge")

    g.add_edge("merge", "synthesizer")
    g.add_edge("synthesizer", END)

    return g.compile()


# Compile eagerly at import to fail fast on graph misconfiguration.
graph = build_graph()


async def run_pipeline(trip_params: TripParams) -> PipelineState:
    """Execute the full pipeline for a trip and return final state."""
    t0 = time.perf_counter()
    logger.info(
        "run_pipeline.start  trip_id=%s  dest=%r  days=%s",
        trip_params.trip_id,
        trip_params.destination,
        trip_params.duration_days,
    )
    initial: PipelineState = {"trip_params": trip_params}
    final = await graph.ainvoke(initial)
    logger.info("run_pipeline.complete  elapsed=%.1fs", time.perf_counter() - t0)
    return final  # type: ignore[return-value]
