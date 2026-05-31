"""One-off backfill: self-host imagery for trips that predate the build-time
image pipeline (``trips.images_resolved_at IS NULL``).

Why this exists
---------------
Images are normally resolved + uploaded to Supabase Storage during the agent's
"building" phase by the SOLE image writer (``app/images.py``), *before* a trip
is marked ready. Trips created before that pipeline shipped — or by an older
running agent — have null image columns and ``images_resolved_at IS NULL``; on
the web they show the deterministic themed fallback instead of a real photo.

This script heals those trips OFF the read path. Resolving on read would
reintroduce the read/write race the pipeline was built to remove, so the heal
is a deliberate offline batch. It reuses the EXACT same resolution / host /
dedup logic as build time (``resolve_and_store_places``), then stamps
``images_resolved_at`` so each trip is healed at most once.

Usage
-----
    uv run python scripts/backfill_images.py             # backfill all eligible
    uv run python scripts/backfill_images.py --dry-run   # resolve + log, no writes
    uv run python scripts/backfill_images.py --limit 20
    uv run python scripts/backfill_images.py --trip-id <uuid>

By default only trips with a terminal-success status (``ready,completed``) are
touched, so the backfill never collides with a trip the live pipeline is still
building (single-writer guarantee). ``--trip-id`` ignores the status filter.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

from app.db import supabase_writer
from app.db.supabase_writer import _get_client
from app.images import resolve_and_store_places

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("backfill_images")


def _fetch_trips(
    statuses: list[str], trip_id: str | None, limit: int | None
) -> list[dict[str, Any]]:
    """Trips still missing imagery (oldest first)."""
    q = (
        _get_client()
        .table("trips")
        .select("id,destination,status,images_resolved_at")
        .is_("images_resolved_at", "null")
    )
    if trip_id:
        q = q.eq("id", trip_id)
    else:
        q = q.in_("status", statuses)
    q = q.order("created_at", desc=False)
    if limit:
        q = q.limit(limit)
    return q.execute().data or []


def _fetch_cities(trip_id: str) -> list[str]:
    rows = (
        _get_client()
        .table("itinerary_days")
        .select("city,day_number")
        .eq("trip_id", trip_id)
        .order("day_number")
        .execute()
    ).data or []
    return [r["city"] for r in rows if r.get("city")]


async def _backfill_one(trip: dict[str, Any], dry_run: bool) -> bool:
    trip_id = trip["id"]
    destination = trip.get("destination") or ""
    cities = await asyncio.to_thread(_fetch_cities, trip_id)
    if not destination and not cities:
        logger.warning("skip trip=%s — no destination/cities to resolve", trip_id)
        return False

    hero_url, city_images = await resolve_and_store_places(trip_id, destination, cities)
    resolved = sum(1 for u in city_images.values() if u)
    verb = "DRY" if dry_run else "healed"
    logger.info(
        "%s trip=%s dest=%r hero=%s cities=%d/%d",
        verb,
        trip_id,
        destination,
        bool(hero_url),
        resolved,
        len(city_images),
    )
    if dry_run:
        return bool(hero_url) or resolved > 0

    await supabase_writer.backfill_trip_images(trip_id, hero_url, city_images)
    return True


async def _amain(args: argparse.Namespace) -> None:
    statuses = [s.strip() for s in args.status.split(",") if s.strip()]
    trips = await asyncio.to_thread(_fetch_trips, statuses, args.trip_id, args.limit)
    logger.info("found %d trip(s) with images_resolved_at IS NULL", len(trips))

    healed = 0
    for trip in trips:
        try:
            if await _backfill_one(trip, args.dry_run):
                healed += 1
        except Exception:  # noqa: BLE001
            logger.exception("backfill failed for trip=%s — continuing", trip.get("id"))

    verb = "would heal" if args.dry_run else "healed"
    logger.info("done: %s %d/%d trip(s)", verb, healed, len(trips))


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill self-hosted trip imagery.")
    p.add_argument("--dry-run", action="store_true", help="resolve + log, no DB writes")
    p.add_argument("--limit", type=int, default=None, help="max trips to process")
    p.add_argument("--trip-id", default=None, help="backfill a single trip by id")
    p.add_argument(
        "--status",
        default="ready,completed",
        help="comma list of trip statuses to include (ignored with --trip-id)",
    )
    asyncio.run(_amain(p.parse_args()))


if __name__ == "__main__":
    main()
