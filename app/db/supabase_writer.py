"""Supabase write layer.

Thin wrapper around supabase-py providing the four mutation entry points
the pipeline needs. All functions are async-friendly (the underlying
client is sync; we use asyncio.to_thread for non-blocking calls).
"""

from __future__ import annotations

import asyncio
from typing import Any

from supabase import Client, create_client

from app.config import settings
from app.schemas import AIItinerary, ResearchDiscovery

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to use the Supabase writer."
            )
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    return _client


async def update_research_job(trip_id: str, **fields: Any) -> None:
    """Patch a research_jobs row identified by trip_id."""
    if not fields:
        return

    def _update() -> None:
        client = _get_client()
        client.table("research_jobs").update(fields).eq("trip_id", trip_id).execute()

    await asyncio.to_thread(_update)


async def write_itinerary(trip_id: str, itinerary: AIItinerary) -> None:
    """Write itinerary_days and stops for a trip.

    Inserts one row per day, then per stop. Day rows are inserted first so
    the returned ids can be used as foreign keys on the stops.
    """

    def _write() -> None:
        client = _get_client()

        # Insert days. stop_count must be populated here — the FE day card
        # ("{N} stops planned") reads it directly, see ItineraryReveal.tsx.
        day_rows = [
            {
                "trip_id": trip_id,
                "day_number": d.dayNumber,
                "city": d.city,
                "title": d.title,
                "description": d.description,
                "highlights": d.highlights,
                "stop_count": len(d.stops),
            }
            for d in itinerary.days
        ]
        days_resp = client.table("itinerary_days").insert(day_rows).execute()
        inserted_days = days_resp.data or []
        # Map day_number -> id for foreign-key linking.
        day_id_by_number: dict[int, Any] = {
            row["day_number"]: row["id"] for row in inserted_days if "id" in row
        }

        # Insert stops.
        stop_rows: list[dict[str, Any]] = []
        for d in itinerary.days:
            day_id = day_id_by_number.get(d.dayNumber)
            for s in d.stops:
                stop_rows.append(
                    {
                        "trip_id": trip_id,
                        "day_id": day_id,
                        "sort_order": s.sortOrder,
                        "time": s.time,
                        "ampm": s.ampm,
                        "duration": s.duration,
                        "name": s.name,
                        "description": s.description,
                        "source": s.source,
                        "tags": s.tags,
                    }
                )
        if stop_rows:
            client.table("stops").insert(stop_rows).execute()

    await asyncio.to_thread(_write)


async def mark_trip_ready(trip_id: str, stats: dict[str, int]) -> None:
    """Mark a trip as ready and store summary stats."""

    def _mark() -> None:
        client = _get_client()
        payload = {
            "status": "ready",
            "stats_places": stats.get("stats_places", 0),
            "stats_tips": stats.get("stats_tips", 0),
            "stats_photo_stops": stats.get("stats_photo_stops", 0),
        }
        client.table("trips").update(payload).eq("id", trip_id).execute()

    await asyncio.to_thread(_mark)


async def update_trip_overview(trip_id: str, itinerary: AIItinerary) -> None:
    """Write the Tier 2 trip-level planning surface onto the trips row.

    Deliberately ISOLATED from `mark_trip_ready`: if these columns don't exist
    yet (Prisma migration not run), this write fails on its own without
    blocking the trip from going 'ready'. The caller wraps it in try/except so
    a missing-column 400 is logged and skipped, not fatal.

    Columns are snake_case (Postgres); they mirror nomad-api's Prisma `Trip`
    fields routeSummary / transportStrategy / seasonalTips / stayByCity /
    budgetEstimate via @map. `stay_by_city` is JSONB; `seasonal_tips` is text[].
    """
    payload = {
        "route_summary": itinerary.route_summary,
        "transport_strategy": itinerary.transport_strategy,
        "seasonal_tips": itinerary.seasonal_tips,
        "stay_by_city": itinerary.stay_by_city,
        "budget_estimate": itinerary.budget_estimate,
    }

    def _write() -> None:
        client = _get_client()
        client.table("trips").update(payload).eq("id", trip_id).execute()

    await asyncio.to_thread(_write)


async def mark_trip_failed(trip_id: str, error_message: str) -> None:
    """Mark a trip and its research job as failed."""

    def _mark() -> None:
        client = _get_client()
        client.table("trips").update({"status": "failed"}).eq("id", trip_id).execute()
        client.table("research_jobs").update(
            {"status": "failed", "message": error_message}
        ).eq("trip_id", trip_id).execute()

    await asyncio.to_thread(_mark)


async def write_discoveries(trip_id: str, discoveries: list[ResearchDiscovery]) -> None:
    """Write the discoveries JSON array into research_jobs.discoveries (JSONB).

    The FE polling loop (useResearchTicker.ts) animates the "LIVE DISCOVERY"
    card whenever this array grows. Called at least twice per pipeline run —
    once mid-flight from merge_node with a partial list, once at the end
    from _run_and_persist with the full list — so the FE swaps cards at
    least twice. See FRONTEND_INTEGRATION_PLAN.md §8 Phase 2.

    Replaces (does not append to) the column. Caller controls cumulative
    behaviour by passing a larger list each call.
    """
    payload = [d.model_dump() for d in discoveries]

    def _write() -> None:
        client = _get_client()
        client.table("research_jobs").update({"discoveries": payload}).eq(
            "trip_id", trip_id
        ).execute()

    await asyncio.to_thread(_write)
