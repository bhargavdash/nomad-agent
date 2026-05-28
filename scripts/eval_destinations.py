"""Eval harness: run the pipeline across destinations and score each (rubric).

Token-spending (runs the synthesizer + geo + research per destination), so run
it deliberately, not in CI. Scoring itself is free/deterministic
(`app.eval.score_itinerary`). Use it to regression-test prompt/agent changes
across destinations instead of eyeballing one Rajasthan run.

Usage:
    uv run python scripts/eval_destinations.py                       # default set
    uv run python scripts/eval_destinations.py samples/kerala-test.json samples/paris-test.json

Honors the L1 cache (set REDIS_URL to make repeat runs cheap) and graceful
degradation (research agents that 429 just return []).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import cache  # noqa: E402
from app.agents.google_blog import run_google_blog_agent  # noqa: E402
from app.agents.reddit import run_reddit_agent  # noqa: E402
from app.agents.synthesizer import run_synthesizer  # noqa: E402
from app.agents.youtube_shorts import run_youtube_agent  # noqa: E402
from app.eval import score_itinerary  # noqa: E402
from app.geo import build_geo_brief  # noqa: E402
from app.observability import configure_observability  # noqa: E402
from app.schemas import AIItinerary, ResearchDiscovery, TripParams  # noqa: E402
from app.signals import (  # noqa: E402
    TravelSignals,
    enrich_anchor_hints,
    enrich_signals_with_llm,
    extract_signals,
)

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

DEFAULT_SAMPLES = [
    "samples/rajasthan-dec20-31.json",
    "samples/kerala-test.json",
    "samples/paris-test.json",
]


async def _build_itinerary(trip: TripParams) -> tuple[AIItinerary, TravelSignals]:
    """Minimal pipeline (no Supabase writes): signals → cache/research → geo → synth."""
    signals = extract_signals(trip)
    signals = await enrich_signals_with_llm(signals, trip)

    all_discoveries = await cache.get_cached_research(trip.destination)
    if all_discoveries is None:
        await enrich_anchor_hints(signals, trip.destination)
        yt = await run_youtube_agent(trip, signals)
        rd = await run_reddit_agent(trip, signals)
        gg = await run_google_blog_agent(trip, signals)
        seeds = [
            ResearchDiscovery(
                id=f"anchor-{i}",
                title=name,
                body=f"{name} — a well-known landmark in {trip.destination}.",
                source="maps",
                tags=["anchor_hint"],
            )
            for i, name in enumerate(signals.top_anchors or [])
        ]
        all_discoveries = [*seeds, *yt, *rd, *gg]
        await cache.set_cached_research(trip.destination, all_discoveries)

    geo_brief = await build_geo_brief(trip, signals)
    return await run_synthesizer(trip, signals, all_discoveries, geo_brief), signals


async def _amain() -> int:
    configure_observability()
    paths = sys.argv[1:] or DEFAULT_SAMPLES
    rows: list[tuple[str, int, dict]] = []
    for p in paths:
        path = ROOT / p if not Path(p).is_absolute() else Path(p)
        try:
            trip = TripParams.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        print(f"\n=== {trip.destination} ({trip.duration_days}d) ===", file=sys.stderr)
        itin, signals = await _build_itinerary(trip)
        result = score_itinerary(itin, trip, signals)
        rows.append((trip.destination, result["score"], result["checks"]))
        for name, ok in result["checks"].items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}", file=sys.stderr)
        print(f"  SCORE: {result['score']}/100", file=sys.stderr)

    print("\n================ EVAL SUMMARY ================")
    for dest, score, _ in rows:
        print(f"  {score:>3}/100  {dest}")
    if rows:
        avg = round(sum(s for _, s, _ in rows) / len(rows))
        print(f"  ----\n  {avg:>3}/100  AVERAGE ({len(rows)} destinations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
