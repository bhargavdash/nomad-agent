"""Run the YouTubeShortsAgent end-to-end against the real YouTube API + real LLM.

Usage:
    uv run python scripts/run_youtube_agent_locally.py

Required env vars (in .env):
    YOUTUBE_API_KEY            — Google Cloud Console, YouTube Data API v3 enabled
    GROQ_API_KEY               — groq.com (free tier, fast Llama 3.3 70B)
      OR change LLM_YOUTUBE_PROVIDER + key for another provider

What it does:
    1. Loads tests/fixtures/sample_trip.json
    2. extract_signals() on it
    3. run_youtube_agent() — real YouTube API call + real LLM extraction
    4. Pretty-prints the resulting ResearchDiscovery list

Cost: ~101 YouTube quota units (out of 10,000/day) + ~1k Groq tokens.
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


async def main() -> None:
    fixture_path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_trip.json"
    with fixture_path.open() as f:
        trip = TripParams(**json.load(f))

    print(f"\n=== Trip ===")
    print(f"Destination : {trip.destination}")
    print(f"Dates       : {trip.date_from} → {trip.date_to}")
    print(f"Vibes       : {', '.join(trip.vibes)}")
    print(f"Pace        : {trip.pace} | Budget: {trip.budget}")

    signals = extract_signals(trip)
    print(f"\n=== Signals ===")
    print(f"Region              : {signals.region}")
    print(f"Season              : {signals.season}")
    print(f"Crowd level         : {signals.crowd_level}")
    print(f"Active festivals    : {signals.active_festivals or '—'}")
    print(f"Source weights      : {signals.vibe_source_weights}")
    print(f"Query modifiers     : {signals.query_modifiers}")
    if signals.warnings:
        print(f"Warnings            :")
        for w in signals.warnings:
            print(f"  - {w}")

    print(f"\n=== Running YouTube agent... ===\n")
    discoveries = await run_youtube_agent(trip, signals)

    print(f"\n=== Discoveries ({len(discoveries)}) ===\n")
    for i, d in enumerate(discoveries, 1):
        print(f"[{i}] {d.title}")
        print(f"    Tags: {', '.join(d.tags)}")
        print(f"    Source: {d.source}")
        print(f"    {d.body}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
