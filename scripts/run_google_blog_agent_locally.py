"""Run the GoogleBlogAgent end-to-end against the live Tavily API + real LLM.

Usage:
    uv run python scripts/run_google_blog_agent_locally.py            # default: Goa
    uv run python scripts/run_google_blog_agent_locally.py paris      # luxury spec check
    uv run python scripts/run_google_blog_agent_locally.py rajasthan
    uv run python scripts/run_google_blog_agent_locally.py manali_monsoon
    uv run python scripts/run_google_blog_agent_locally.py bali
    uv run python scripts/run_google_blog_agent_locally.py all        # all fixtures

Required env vars (in .env):
    TAVILY_API_KEY           — tavily.com (free tier: 1000 searches/month)
    GROQ_API_KEY             — groq.com (default LLM_GOOGLE_PROVIDER)
      OR set LLM_GOOGLE_PROVIDER + matching key for another provider

Cost per run:
    ~3-4 Tavily searches + ~4k Groq tokens (single-pass extraction).
    Well within free tiers for testing.

Quality checks printed:
    1. Raw Tavily results — verify search returns real blog content, not SEO junk.
    2. Extracted discoveries — verify LLM produces concrete, non-vague output.
    3. Summary metrics — count of raw results, extracted vs kept ratio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

# Force UTF-8 on Windows so unicode / emoji in blog titles don't crash cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.google_blog import (  # noqa: E402
    _build_queries,
    _extract_via_llm,
    _to_research_discoveries,
    _validate_and_dedupe,
    run_google_blog_agent,
)
from app.schemas import TripParams  # noqa: E402
from app.signals import extract_signals  # noqa: E402
from app.tools.tavily_search import search_fanout  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Built-in fixture for the spec's "done when" check:
# "≥5 blog-sourced discoveries for Paris August luxury"
_BUILT_IN_FIXTURES: dict[str, dict] = {
    "paris": {
        "trip_id": "00000000-0000-0000-0000-000000000010",
        "user_id": "00000000-0000-0000-0000-0000000000aa",
        "destination": "Paris, France",
        "date_from": "2026-08-10",
        "date_to": "2026-08-17",
        "duration_days": 7,
        "travelers": "2",
        "vibes": ["luxury", "culture", "iconic"],
        "accommodation": "Boutique Hotel",
        "pace": "Balanced",
        "budget": "Very-High",
        "preferences": None,
    },
    "tokyo": {
        "trip_id": "00000000-0000-0000-0000-000000000011",
        "user_id": "00000000-0000-0000-0000-0000000000aa",
        "destination": "Tokyo, Japan",
        "date_from": "2026-09-05",
        "date_to": "2026-09-12",
        "duration_days": 7,
        "travelers": "2",
        "vibes": ["foodie", "culture", "street food"],
        "accommodation": "Boutique Hotel",
        "pace": "Action-Packed",
        "budget": "High",
        "preferences": None,
    },
}

# Vague-phrase detector — mirror of the one in the agent, used for QA output.
_VAGUE_RE = re.compile(
    r"\bstunning\b|\bbreathtaking\b|\bpicturesque\b|\bvibrant\s+culture\b"
    r"|\bmust[-\s]?visit\b|\bsomething\s+for\s+everyone\b|\bworld[-\s]?class\b"
    r"|\brich\s+culture\b|\bnatural\s+beauty\b",
    re.IGNORECASE,
)


def _resolve_fixture(arg: str | None) -> TripParams:
    if not arg or arg in {"default", "goa"}:
        return TripParams(**json.loads((FIXTURES_DIR / "sample_trip.json").read_text()))
    if arg in _BUILT_IN_FIXTURES:
        return TripParams(**_BUILT_IN_FIXTURES[arg])
    candidate = Path(arg)
    if candidate.exists():
        return TripParams(**json.loads(candidate.read_text()))
    short = FIXTURES_DIR / f"sample_trip_{arg}.json"
    if short.exists():
        return TripParams(**json.loads(short.read_text()))
    raise FileNotFoundError(f"No fixture for {arg!r} — tried {short}")


async def _run_one(trip: TripParams) -> None:
    signals = extract_signals(trip)

    print(f"\n{'=' * 70}")
    print(f"  Destination : {trip.destination}")
    print(f"  Dates       : {trip.date_from} -> {trip.date_to}")
    print(f"  Vibes       : {', '.join(trip.vibes)}")
    print(f"  Pace        : {trip.pace}  |  Budget: {trip.budget}")
    print(f"{'=' * 70}")

    print("\n=== Signals ===")
    print(f"  Region           : {signals.region}")
    print(f"  Season           : {signals.season}")
    print(f"  Crowd level      : {signals.crowd_level}")
    print(f"  Budget tier      : {signals.budget_tier}")
    print(f"  Active festivals : {signals.active_festivals or '(none)'}")
    print(f"  Query modifiers  : {signals.query_modifiers}")
    if signals.warnings:
        for w in signals.warnings:
            print(f"  WARNING: {w}")

    # --- Step 1: show the queries being built ---
    queries = _build_queries(trip, signals)
    print(f"\n=== Tavily Queries ({len(queries)}) ===")
    for i, q in enumerate(queries, 1):
        print(f"  [{i}] {q!r}")

    # --- Step 2: fetch raw Tavily results and show them ---
    print("\n=== Raw Tavily Results (before LLM) ===")
    articles = await search_fanout(queries, max_results_per_query=5)
    if not articles:
        print("  (!) No results returned — check TAVILY_API_KEY")
        return

    print(f"  {len(articles)} unique articles fetched\n")
    for i, a in enumerate(articles, 1):
        score_bar = "#" * int(a.score * 10)
        print(f"  [{i}] {a.title or '(no title)'}")
        print(f"       URL   : {a.url}")
        print(f"       Score : {a.score:.2f}  [{score_bar:<10}]")
        excerpt = " ".join(a.content.split())
        if len(excerpt) > 200:
            excerpt = excerpt[:200] + "..."
        print(f"       Blurb : {excerpt}")
        print()

    # --- Step 3: LLM extraction ---
    print("=== LLM Extraction ===")
    capped = articles[:15]
    extracted = await _extract_via_llm(trip, signals, capped)
    print(f"  LLM returned {len(extracted)} raw places")

    validated = _validate_and_dedupe(extracted, n_articles=len(capped))
    discoveries = _to_research_discoveries(validated)
    print(f"  Kept after validation: {len(validated)}")

    # --- Step 4: show final discoveries with quality indicators ---
    print(f"\n=== Final Discoveries ({len(discoveries)}) ===\n")

    if not discoveries:
        print("  (!) 0 discoveries returned — check LLM output above in logs")
        return

    vague_count = 0
    short_count = 0

    for i, d in enumerate(discoveries, 1):
        vague_hit = _VAGUE_RE.search(d.body)
        length_ok = len(d.body) >= 60
        flag = ""
        if vague_hit:
            flag += f"  [!] VAGUE phrase in body: {vague_hit.group(0)!r}"
            vague_count += 1
        if not length_ok:
            flag += "  [!] BODY TOO SHORT"
            short_count += 1

        print(f"[{i}] {d.title}")
        print(f"     Source : {d.source}")
        print(f"     Tags   : {', '.join(d.tags)}")
        print(f"     Body   : {d.body}")
        if flag:
            print(flag)
        print()

    # --- Step 5: pass/fail summary ---
    print(f"{'=' * 70}")
    print("  QUALITY SUMMARY")
    print(f"{'=' * 70}")
    passed = len(discoveries) >= 5
    print(f"  Count     : {len(discoveries)} ({'PASS' if passed else 'FAIL — need >= 5'})")
    print(f"  Vague     : {vague_count} ({'PASS' if vague_count == 0 else 'WARN — vague phrases detected'})")
    print(f"  Too short : {short_count} ({'PASS' if short_count == 0 else 'WARN'})")
    sources_used = {d.source for d in discoveries}
    print(f"  Sources   : {sources_used}")
    if passed and vague_count == 0:
        print("  => LOOKS GOOD: concrete, non-vague blog content")
    else:
        print("  => NEEDS REVIEW: see warnings above")
    print()


async def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    if arg == "all":
        trips = [
            ("goa", _resolve_fixture("goa")),
            ("paris", _resolve_fixture("paris")),
            ("manali_monsoon", _resolve_fixture("manali_monsoon")),
            ("bali", _resolve_fixture("bali")),
        ]
        for name, trip in trips:
            print(f"\n\n{'#' * 70}")
            print(f"#  FIXTURE: {name}")
            print(f"{'#' * 70}")
            await _run_one(trip)
    else:
        trip = _resolve_fixture(arg)
        await _run_one(trip)


if __name__ == "__main__":
    asyncio.run(main())
