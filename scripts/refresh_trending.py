"""Manually trigger a trending refresh for a given season key.

Runs the same pipeline as POST /agent/trending-refresh:
  generate_trending  →  resolve_and_store_trending_images  →  write_trending

Usage (from nomad-agent/):
    uv run python scripts/refresh_trending.py
    uv run python scripts/refresh_trending.py --season-key monsoon-2026
    uv run python scripts/refresh_trending.py --season monsoon --year 2026

Required env vars (in .env):
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    LLM_TRENDING_API_KEY   (or whichever key the trending LLM factory reads)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("refresh_trending")


async def main(season_key: str, season: str, year: int) -> None:
    from app.agents.trending import generate_trending, Season
    from app.db import supabase_writer
    from app.images import resolve_and_store_trending_images

    logger.info("Generating trending payload  season=%s  year=%s  key=%s", season, year, season_key)
    payload = await generate_trending(season=season, year=year)  # type: ignore[arg-type]

    logger.info("India destinations generated:")
    for d in payload.india:
        logger.info("  %-22s  %s", d.name, d.country)

    logger.info("International destinations generated:")
    for d in payload.international:
        logger.info("  %-22s  %s", d.name, d.country)

    logger.info("Resolving + storing destination images...")
    await resolve_and_store_trending_images(payload)

    logger.info("Writing to trending_cache  key=%s", season_key)
    await supabase_writer.write_trending(season_key, payload)

    logger.info("Done — trending_cache upserted for key=%s", season_key)

    print(json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trigger a trending cache refresh")
    parser.add_argument("--season-key", default=None, help="e.g. monsoon-2026")
    parser.add_argument("--season", default=None, help="summer|monsoon|post-monsoon|winter")
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args()

    from app.agents.trending import current_season, current_season_key
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    if args.season_key:
        try:
            season_str, year_str = args.season_key.rsplit("-", 1)
            season = season_str
            year = int(year_str)
            season_key = args.season_key
        except (ValueError, TypeError):
            print(f"ERROR: invalid --season-key '{args.season_key}'. Expected 'season-year'.", file=sys.stderr)
            sys.exit(1)
    else:
        season = args.season or current_season(now)
        year = args.year or now.year
        season_key = f"{season}-{year}"

    valid_seasons = ("summer", "monsoon", "post-monsoon", "winter")
    if season not in valid_seasons:
        print(f"ERROR: unknown season '{season}'. Must be one of {valid_seasons}.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(season_key=season_key, season=season, year=year))
