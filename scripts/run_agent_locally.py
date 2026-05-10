"""Run the LangGraph pipeline end-to-end against a fixture trip.

Bypasses FastAPI for fast iteration during agent development.

Usage:
    uv run python scripts/run_agent_locally.py
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows so emoji/unicode prints don't crash cp1252.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make the project root importable when run as a script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.graph.pipeline import run_pipeline  # noqa: E402
from app.schemas import TripParams  # noqa: E402

FIXTURE = ROOT | "tests" | "fixtures" | "sample_trip.json"


async def main() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    trip = TripParams.model_validate(raw)

    print(f"=== Running pipeline for trip {trip.trip_id} → {trip.destination} ===")
    final = await run_pipeline(trip)

    signals = final.get("signals")
    itinerary = final.get("itinerary")
    discoveries = final.get("all_discoveries", [])

    print("\n--- Signals ---")
    print(signals)

    print(f"\n--- Discoveries ({len(discoveries)} total) ---")
    for d in discoveries:
        print(f"  [{d.source}] {d.title}")

    print("\n--- Itinerary ---")
    if itinerary is not None:
        print(itinerary.model_dump_json(indent=2))
    else:
        print("(no itinerary produced)")


if __name__ == "__main__":
    asyncio.run(main())
