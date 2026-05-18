"""Run the RedditAgent end-to-end against the live Reddit JSON API + real LLM.

Usage:
    uv run python scripts/run_reddit_agent_locally.py
    uv run python scripts/run_reddit_agent_locally.py rajasthan
    uv run python scripts/run_reddit_agent_locally.py manali_monsoon
    uv run python scripts/run_reddit_agent_locally.py path/to/trip.json

Required env vars (in .env):
    GROQ_API_KEY (or whatever LLM_REDDIT_PROVIDER is configured for)

Reddit JSON API needs no auth, but the User-Agent header MUST be custom —
the tool sets one for us. Rate limit (60 req/min) is respected via the 1s
sleep between calls in `search_many_with_rate_limit`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows so unicode arrows etc. don't blow up cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

# Make `app` importable when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.reddit import run_reddit_agent  # noqa: E402
from app.schemas import TripParams  # noqa: E402
from app.signals import extract_signals  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _resolve_fixture(arg: str | None) -> Path:
    if not arg or arg in {"default", "goa"}:
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
    print(f"Query modifiers     : {signals.query_modifiers}")

    print("\n=== Running Reddit agent... ===\n")
    discoveries = await run_reddit_agent(trip, signals)

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
