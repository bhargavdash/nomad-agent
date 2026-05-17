"""Run the YouTubeLongformAgent end-to-end against the real YouTube API + real LLM.

Usage:
    # Default: runs against tests/fixtures/sample_trip.json (Goa)
    uv run python scripts/run_youtube_longform_agent_locally.py

    # Pick a fixture by short name (sample_trip.json or sample_trip_<name>.json)
    uv run python scripts/run_youtube_longform_agent_locally.py rajasthan
    uv run python scripts/run_youtube_longform_agent_locally.py goa

    # Run all known fixtures back-to-back for comparison
    uv run python scripts/run_youtube_longform_agent_locally.py all

    # Or pass a full path
    uv run python scripts/run_youtube_longform_agent_locally.py path/to/trip.json

Required env vars (in .env):
    YOUTUBE_API_KEY                       — Google Cloud Console, YouTube Data API v3
    GROQ_API_KEY                          — groq.com (or change provider per role)
    LLM_YOUTUBE_LONGFORM_PROVIDER/MODEL   — optional overrides; defaults match Shorts

Cost per run:
  ~4 search.list (400 units) + ~4 videos.list (4 units) ≈ 404 YouTube quota
  + transcript fetches (free) + ~4-6 Groq Pass-1 calls + 1 Pass-2 call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Make `app` importable when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.youtube_longform import run_youtube_longform_agent  # noqa: E402
from app.schemas import TripParams  # noqa: E402
from app.signals import extract_signals  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


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
    if signals.warnings:
        print("Warnings            :")
        for w in signals.warnings:
            print(f"  - {w}")

    print("\n=== Running YouTube long-form agent... ===\n")
    discoveries = await run_youtube_longform_agent(trip, signals)

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
