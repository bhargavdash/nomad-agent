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
from app import cache
from app.db import supabase_writer
from app.geo import GeoBrief, build_geo_brief
from app.schemas import AIItinerary, ResearchDiscovery, TripParams
from app.signals import TravelSignals, enrich_anchor_hints, enrich_signals_with_llm, extract_signals

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    trip_params: TripParams
    signals: TravelSignals
    geo_brief: GeoBrief
    # L1 cache (Milestone C): cached destination research pool on a HIT, None on
    # a miss. Set by research_gate; read by the research nodes (to no-op) + merge.
    research_cache: list[ResearchDiscovery] | None
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
    # NOTE: anchor-hint enrichment (an LLM call) moved to research_gate so it
    # only runs on a cache MISS — on a HIT the cached pool already has anchors.
    logger.info(
        "[NODE] signals → done  region=%s season=%s crowd=%s budget=%s  (%.1fs)",
        signals.region,
        signals.season,
        signals.crowd_level,
        signals.budget_tier,
        time.perf_counter() - t0,
    )
    return {"signals": signals}


async def research_gate_node(state: PipelineState) -> dict[str, Any]:
    """L1 cache gate. On HIT, set research_cache so the research agents no-op and
    merge uses the cached pool. On MISS, run the (LLM) anchor enrichment that the
    research path needs, and signal the agents to run."""
    destination = state["trip_params"].destination
    cached = await cache.get_cached_research(destination)
    if cached is not None:
        logger.info(
            "[NODE] research_gate → CACHE HIT n=%d (skipping research agents)",
            len(cached),
        )
        return {"research_cache": cached}
    logger.info("[NODE] research_gate → cache miss (running research)")
    # Anchors are only needed when we actually research (they get baked into the
    # cached pool). Skipping on hit also avoids an extra LLM call.
    await enrich_anchor_hints(state["signals"], destination)
    return {"research_cache": None}


async def geo_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    logger.info("[NODE] geo → starting")
    brief = await build_geo_brief(state["trip_params"], state["signals"])
    logger.info(
        "[NODE] geo → done  cities=%d legs=%d sun=%d reordered=%s  (%.1fs)",
        len(brief.ordered_cities),
        len(brief.legs),
        len(brief.sun),
        brief.reordered,
        time.perf_counter() - t0,
    )
    return {"geo_brief": brief}


async def youtube_node(state: PipelineState) -> dict[str, Any]:
    if state.get("research_cache") is not None:
        return {"yt_discoveries": []}  # cache hit — skip the API/LLM work
    t0 = time.perf_counter()
    logger.info("[NODE] youtube_shorts → starting")
    discoveries = await run_youtube_agent(state["trip_params"], state["signals"])
    logger.info(
        "[NODE] youtube_shorts → done  discoveries=%d  (%.1fs)",
        len(discoveries),
        time.perf_counter() - t0,
    )
    return {"yt_discoveries": discoveries}


async def youtube_longform_node(state: PipelineState) -> dict[str, Any]:
    if state.get("research_cache") is not None:
        return {"yt_longform_discoveries": []}  # cache hit — skip
    t0 = time.perf_counter()
    logger.info("[NODE] youtube_longform → starting")
    discoveries = await run_youtube_longform_agent(state["trip_params"], state["signals"])
    logger.info(
        "[NODE] youtube_longform → done  discoveries=%d  (%.1fs)",
        len(discoveries),
        time.perf_counter() - t0,
    )
    return {"yt_longform_discoveries": discoveries}


async def reddit_node(state: PipelineState) -> dict[str, Any]:
    if state.get("research_cache") is not None:
        return {"reddit_discoveries": []}  # cache hit — skip
    t0 = time.perf_counter()
    logger.info("[NODE] reddit → starting")
    discoveries = await run_reddit_agent(state["trip_params"], state["signals"])
    logger.info(
        "[NODE] reddit → done  discoveries=%d  (%.1fs)", len(discoveries), time.perf_counter() - t0
    )
    return {"reddit_discoveries": discoveries}


async def google_node(state: PipelineState) -> dict[str, Any]:
    if state.get("research_cache") is not None:
        return {"google_discoveries": []}  # cache hit — skip
    t0 = time.perf_counter()
    logger.info("[NODE] google_blog → starting")
    discoveries = await run_google_blog_agent(state["trip_params"], state["signals"])
    logger.info(
        "[NODE] google_blog → done  discoveries=%d  (%.1fs)",
        len(discoveries),
        time.perf_counter() - t0,
    )
    return {"google_discoveries": discoveries}


async def merge_node(state: PipelineState) -> dict[str, Any]:
    t0 = time.perf_counter()
    destination = state["trip_params"].destination

    # --- L1 cache HIT: use the cached pool, skip concat/seed/cache-write. ------
    cached = state.get("research_cache")
    if cached is not None:
        all_discoveries = cached
        if all_discoveries:
            chunk_size = max(1, min(5, len(all_discoveries) // 2))
            try:
                await supabase_writer.write_discoveries(
                    state["trip_params"].trip_id, all_discoveries[:chunk_size]
                )
            except Exception:  # noqa: BLE001
                logger.warning("merge_node: mid-flight write failed (cache hit)", exc_info=True)
        logger.info(
            "[NODE] merge → CACHE HIT  all_discoveries=%d  (%.1fs)",
            len(all_discoveries),
            time.perf_counter() - t0,
        )
        return {"all_discoveries": all_discoveries}

    # --- L1 cache MISS: concat research, seed anchors, then cache the pool. ----
    yt = state.get("yt_discoveries", []) or []
    ytl = state.get("yt_longform_discoveries", []) or []
    red = state.get("reddit_discoveries", []) or []
    goog = state.get("google_discoveries", []) or []
    logger.info(
        "[NODE] merge → yt_shorts=%d  yt_longform=%d  reddit=%d  google=%d  total_in=%d",
        len(yt),
        len(ytl),
        len(red),
        len(goog),
        len(yt) + len(ytl) + len(red) + len(goog),
    )
    merged: list[ResearchDiscovery] = []
    merged.extend(yt)
    merged.extend(ytl)
    merged.extend(red)
    merged.extend(goog)

    # Pre-seed canonical anchor stops to bypass the extraction LLM's vibe bias.
    # Only add seeds for anchors not already covered by real research (fuzzy match).
    existing_lower = {d.title.lower() for d in merged}
    anchor_seeds: list[ResearchDiscovery] = []
    for name in state["signals"].top_anchors or []:
        name_lower = name.lower()
        already_covered = any(
            name_lower in existing or existing in name_lower for existing in existing_lower
        )
        if not already_covered:
            anchor_seeds.append(
                ResearchDiscovery(
                    id=str(uuid.uuid4()),
                    title=name,
                    body=(
                        f"{name} — a well-known landmark in {destination}. "
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
            await supabase_writer.write_discoveries(state["trip_params"].trip_id, first_chunk)
        except Exception:  # noqa: BLE001
            # Discoveries are a polish — never fail the pipeline over a
            # write error here. Final write in _run_and_persist still tries.
            logger.warning(
                "merge_node: mid-flight write_discoveries failed for trip %s",
                state["trip_params"].trip_id,
                exc_info=True,
            )

    # Cache the freshly-researched pool for this destination (best-effort).
    await cache.set_cached_research(destination, all_discoveries)

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
        state.get("geo_brief"),
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
    g.add_node("geo", geo_node)
    g.add_node("research_gate", research_gate_node)
    g.add_node("youtube", youtube_node)
    g.add_node("youtube_longform", youtube_longform_node)
    g.add_node("reddit", reddit_node)
    g.add_node("google", google_node)
    g.add_node("merge", merge_node)
    g.add_node("synthesizer", synthesizer_node)

    g.add_edge(START, "signals")

    # geo runs in parallel with the research path (both after signals).
    g.add_edge("signals", "geo")

    # research_gate (L1 cache) gates the 4 research agents. On a cache hit it
    # sets research_cache so the agents no-op and merge uses the cached pool.
    g.add_edge("signals", "research_gate")
    g.add_edge("research_gate", "youtube")
    g.add_edge("research_gate", "youtube_longform")
    g.add_edge("research_gate", "reddit")
    g.add_edge("research_gate", "google")

    # Fan-in to merge — LangGraph waits for all incoming edges.
    g.add_edge("youtube", "merge")
    g.add_edge("youtube_longform", "merge")
    g.add_edge("reddit", "merge")
    g.add_edge("google", "merge")

    # Synthesizer waits for BOTH merge (discoveries) and geo (the geo brief).
    g.add_edge("merge", "synthesizer")
    g.add_edge("geo", "synthesizer")
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
    itinerary = final.get("itinerary")
    logger.info(
        "run_pipeline.complete  elapsed=%.1fs  cache_hit=%s  discoveries=%d  days=%d",
        time.perf_counter() - t0,
        final.get("research_cache") is not None,
        len(final.get("all_discoveries") or []),
        len(itinerary.days) if itinerary else 0,
    )
    return final  # type: ignore[return-value]
