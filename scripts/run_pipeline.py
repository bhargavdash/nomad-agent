"""End-to-end driver for the Nomad agent pipeline.

Sprint 2 AI-6 artefact: this is the script you run to see the full AI data
flow as JSON for a single trip input. Runs the 3 research agents
**sequentially** (not via LangGraph) so the per-stage output reads linearly
in stdout. LangGraph parallel orchestration is exercised separately via
`scripts/run_agent_locally.py` (Sprint 3 AI-9).

Usage:
    uv run python scripts/run_pipeline.py samples/goa-december.json
    uv run python scripts/run_pipeline.py samples/manali-monsoon.json
    uv run python scripts/run_pipeline.py path/to/any/trip.json

Required env vars (in `.env`):
    YOUTUBE_API_KEY      — for the YouTube Shorts agent
    TAVILY_API_KEY       — for the Google Blog agent
    GROQ_API_KEY         — for Groq-backed agents (default for YT/Reddit/Google)
    ANTHROPIC_API_KEY    — for the Synthesizer (default: Claude Sonnet)

Reddit JSON API needs no auth.

Output:
    Stage-by-stage banner output to stderr (signals → YT → Reddit → Google
    → merge → synthesizer), then the final `AIItinerary` JSON to stdout so
    you can pipe / redirect:

        uv run python scripts/run_pipeline.py samples/goa-december.json > goa.json
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
from pathlib import Path

# Force UTF-8 on Windows so emoji and the rupee symbol don't blow up cp1252.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Make `app` importable when invoked from the project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.google_blog import run_google_blog_agent  # noqa: E402
from app.agents.reddit import run_reddit_agent  # noqa: E402
from app.agents.synthesizer import run_synthesizer  # noqa: E402
from app.agents.youtube_shorts import run_youtube_agent  # noqa: E402
from app.schemas import ResearchDiscovery, TripParams  # noqa: E402
from app.signals import extract_signals  # noqa: E402


SAMPLES_DIR = ROOT / "samples"


# ---------------------------------------------------------------------------
# Logging — everything goes to stderr so stdout is reserved for the JSON
# artefact. That keeps `> out.json` redirection clean.
# ---------------------------------------------------------------------------


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)


def _banner(title: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n  {title}\n{bar}", file=sys.stderr)


def _subbanner(title: str) -> None:
    print(f"\n--- {title} ---", file=sys.stderr)


def _print_discovery_summary(label: str, ds: list[ResearchDiscovery]) -> None:
    print(f"\n{label}: {len(ds)} discoveries", file=sys.stderr)
    for i, d in enumerate(ds, start=1):
        body_one_line = " ".join(d.body.split())
        if len(body_one_line) > 120:
            body_one_line = body_one_line[:120] + "…"
        print(
            f"  [{i:>2}] ({d.source:>7}) {d.title}\n"
            f"       tags={d.tags}  body={body_one_line}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_trip_path(arg: str | None) -> Path:
    """Resolve a sample short-name OR a path to a TripParams JSON file."""
    if not arg:
        return SAMPLES_DIR / "goa-december.json"
    candidate = Path(arg)
    if candidate.exists():
        return candidate
    # Try as a bare short-name under samples/
    fallback = SAMPLES_DIR / arg
    if fallback.suffix == "":
        fallback = fallback.with_suffix(".json")
    if fallback.exists():
        return fallback
    raise FileNotFoundError(
        f"No TripParams JSON found for arg={arg!r}. "
        f"Tried {candidate} and {fallback}."
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_pipeline_sequential(trip: TripParams) -> dict:
    """Run signals → 3 agents (sequentially) → synthesizer.

    Returns a dict so callers can introspect per-stage output before the
    final JSON dump.
    """
    _banner(f"Trip: {trip.destination}  ({trip.date_from} → {trip.date_to})")
    print(
        f"Duration  : {trip.duration_days} days\n"
        f"Travelers : {trip.travelers}\n"
        f"Vibes     : {', '.join(trip.vibes) if trip.vibes else '—'}\n"
        f"Pace      : {trip.pace}\n"
        f"Budget    : {trip.budget}\n"
        f"Accommo.  : {trip.accommodation}",
        file=sys.stderr,
    )

    _banner("Stage 1 — Signals (pure Python, no LLM)")
    signals = extract_signals(trip)
    print(
        f"Region          : {signals.region}\n"
        f"Season          : {signals.season}\n"
        f"Crowd level     : {signals.crowd_level}\n"
        f"Budget tier     : {signals.budget_tier}\n"
        f"Pace density    : {signals.pace_density} stops/day target\n"
        f"Active festivals: {signals.active_festivals or '—'}\n"
        f"Weather hint    : {signals.weather_hint or '—'}\n"
        f"Query modifiers : {signals.query_modifiers}\n"
        f"Warnings        : {signals.warnings or '—'}\n"
        f"Vibe→source wts : {signals.vibe_source_weights}",
        file=sys.stderr,
    )

    _banner("Stage 2 — YouTubeShorts agent (sequential)")
    yt_discoveries = await run_youtube_agent(trip, signals)
    _print_discovery_summary("YouTube", yt_discoveries)

    _banner("Stage 3 — Reddit agent (sequential)")
    reddit_discoveries = await run_reddit_agent(trip, signals)
    _print_discovery_summary("Reddit", reddit_discoveries)

    _banner("Stage 4 — Google Blog agent (sequential)")
    google_discoveries = await run_google_blog_agent(trip, signals)
    _print_discovery_summary("Google Blog", google_discoveries)

    _banner("Stage 5 — Merge")
    all_discoveries: list[ResearchDiscovery] = [
        *yt_discoveries,
        *reddit_discoveries,
        *google_discoveries,
    ]
    print(
        f"Total discoveries: {len(all_discoveries)}  "
        f"(yt={len(yt_discoveries)}  reddit={len(reddit_discoveries)}  "
        f"blog={len(google_discoveries)})",
        file=sys.stderr,
    )

    _banner("Stage 6 — Synthesizer (LLM call)")
    itinerary = await run_synthesizer(trip, signals, all_discoveries)
    print(
        f"Days       : {len(itinerary.days)}\n"
        f"Total stops: {sum(len(d.stops) for d in itinerary.days)}\n"
        f"Discoveries: {len(itinerary.discoveries)}\n"
        f"Emoji      : {itinerary.emoji}\n"
        f"Stats      : places={itinerary.stats_places} "
        f"tips={itinerary.stats_tips} photo_stops={itinerary.stats_photo_stops}",
        file=sys.stderr,
    )

    # Per-stop source breakdown (useful for tracing data flow).
    _subbanner("Per-stop source breakdown")
    by_source: dict[str, int] = {}
    for day in itinerary.days:
        for stop in day.stops:
            by_source[stop.source] = by_source.get(stop.source, 0) + 1
            print(
                f"  Day {day.dayNumber} #{stop.sortOrder:<2} "
                f"{stop.time:>5} {stop.ampm}  "
                f"({stop.source:>7})  {stop.name}",
                file=sys.stderr,
            )
    _subbanner("Source totals across all stops")
    for src, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {src:>7}: {n}", file=sys.stderr)

    return {
        "trip": trip,
        "signals": signals,
        "yt_discoveries": yt_discoveries,
        "reddit_discoveries": reddit_discoveries,
        "google_discoveries": google_discoveries,
        "all_discoveries": all_discoveries,
        "itinerary": itinerary,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _amain() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        trip_path = _resolve_trip_path(arg)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"Loading trip from: {trip_path}", file=sys.stderr)
    raw = json.loads(trip_path.read_text(encoding="utf-8"))
    trip = TripParams.model_validate(raw)

    state = await run_pipeline_sequential(trip)
    itinerary = state["itinerary"]

    _banner("Final AIItinerary JSON (stdout)")
    # Sole stdout write — everything else goes to stderr — so consumers can
    # pipe / redirect this cleanly.
    sys.stdout.write(itinerary.model_dump_json(indent=2))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
