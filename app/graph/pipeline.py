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

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.google_blog import run_google_blog_agent
from app.agents.reddit import run_reddit_agent
from app.agents.synthesizer import run_synthesizer
from app.agents.youtube_longform import run_youtube_longform_agent
from app.agents.youtube_shorts import run_youtube_agent
from app.schemas import AIItinerary, ResearchDiscovery, TripParams
from app.signals import TravelSignals, extract_signals


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
    signals = extract_signals(state["trip_params"])
    return {"signals": signals}


async def youtube_node(state: PipelineState) -> dict[str, Any]:
    discoveries = await run_youtube_agent(state["trip_params"], state["signals"])
    return {"yt_discoveries": discoveries}


async def youtube_longform_node(state: PipelineState) -> dict[str, Any]:
    discoveries = await run_youtube_longform_agent(
        state["trip_params"], state["signals"]
    )
    return {"yt_longform_discoveries": discoveries}


async def reddit_node(state: PipelineState) -> dict[str, Any]:
    discoveries = await run_reddit_agent(state["trip_params"], state["signals"])
    return {"reddit_discoveries": discoveries}


async def google_node(state: PipelineState) -> dict[str, Any]:
    discoveries = await run_google_blog_agent(state["trip_params"], state["signals"])
    return {"google_discoveries": discoveries}


async def merge_node(state: PipelineState) -> dict[str, Any]:
    merged: list[ResearchDiscovery] = []
    merged.extend(state.get("yt_discoveries", []) or [])
    merged.extend(state.get("yt_longform_discoveries", []) or [])
    merged.extend(state.get("reddit_discoveries", []) or [])
    merged.extend(state.get("google_discoveries", []) or [])
    return {"all_discoveries": merged}


async def synthesizer_node(state: PipelineState) -> dict[str, Any]:
    itinerary = await run_synthesizer(
        state["trip_params"],
        state["signals"],
        state.get("all_discoveries", []),
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
    initial: PipelineState = {"trip_params": trip_params}
    final = await graph.ainvoke(initial)
    return final  # type: ignore[return-value]
