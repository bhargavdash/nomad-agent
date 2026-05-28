"""Run the YouTubeShortsAgent end-to-end against the real YouTube API + real LLM.

Usage:
    # Default: runs against tests/fixtures/sample_trip.json (Goa)
    uv run python scripts/run_youtube_agent_locally.py

    # Pick a fixture by short name (sample_trip.json or sample_trip_<name>.json)
    uv run python scripts/run_youtube_agent_locally.py rajasthan
    uv run python scripts/run_youtube_agent_locally.py goa

    # Run all known fixtures back-to-back for comparison
    uv run python scripts/run_youtube_agent_locally.py all

    # Or pass a full path
    uv run python scripts/run_youtube_agent_locally.py path/to/trip.json

Required env vars (in .env):
    YOUTUBE_API_KEY            — Google Cloud Console, YouTube Data API v3 enabled
    GROQ_API_KEY               — groq.com (free tier, fast Llama 3.3 70B)
      OR change LLM_YOUTUBE_PROVIDER + key for another provider

Cost per run: ~5 search.list (500 units) + ~5 videos.list (5 units) ≈ 505
YouTube quota (out of 10,000/day) + ~6k Groq tokens for two-pass extraction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Make `app` importable when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.youtube_shorts import run_youtube_agent  # noqa: E402
from app.schemas import TripParams  # noqa: E402
from app.signals import extract_signals  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _resolve_fixture(arg: str | None) -> Path:
    """Map a CLI arg to a fixture path. Accepts short names, full paths, or None."""
    if not arg or arg == "default" or arg == "goa":
        return FIXTURES_DIR / "sample_trip.json"
    candidate = Path(arg)
    if candidate.exists():
        return candidate
    short = FIXTURES_DIR / f"sample_trip_{arg}.json"
    if short.exists():
        return short
    raise FileNotFoundError(f"No fixture for arg={arg!r} (tried {short})")


async def _run_one(fixture_path: Path) -> None:
    with fixture_path.open() as f:
        trip = TripParams(**json.load(f))

    print(f"\n{'=' * 70}")
    print(f"  Fixture: {fixture_path.name}")
    print(f"{'=' * 70}")
    print("\n=== Trip ===")
    print(f"Destination : {trip.destination}")
    print(f"Dates       : {trip.date_from} → {trip.date_to}")
    print(f"Vibes       : {', '.join(trip.vibes)}")
    print(f"Pace        : {trip.pace} | Budget: {trip.budget}")

    signals = extract_signals(trip)
    print("\n=== Signals ===")
    print(f"Region              : {signals.region}")
    print(f"Season              : {signals.season}")
    print(f"Crowd level         : {signals.crowd_level}")
    print(f"Active festivals    : {signals.active_festivals or '—'}")
    print(f"Source weights      : {signals.vibe_source_weights}")
    print(f"Query modifiers     : {signals.query_modifiers}")
    if signals.warnings:
        print("Warnings            :")
        for w in signals.warnings:
            print(f"  - {w}")

    print("\n=== Running YouTube agent... ===\n")
    discoveries = await run_youtube_agent(trip, signals)

    print(f"\n=== Discoveries ({len(discoveries)}) ===\n")
    for i, d in enumerate(discoveries, 1):
        print(f"[{i}] {d.title}")
        print(f"    Tags: {', '.join(d.tags)}")
        print(f"    Source: {d.source}")
        print(f"    {d.body}")
        print()


async def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "all":
        for path in [
            FIXTURES_DIR / "sample_trip.json",
            FIXTURES_DIR / "sample_trip_rajasthan.json",
        ]:
            await _run_one(path)
    else:
        await _run_one(_resolve_fixture(arg))


if __name__ == "__main__":
    asyncio.run(main())
