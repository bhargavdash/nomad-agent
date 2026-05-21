"""POST /agent/research — fire-and-forget pipeline trigger."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import JSONResponse

from app.auth import verify_internal_secret
from app.db import supabase_writer
from app.graph.pipeline import run_pipeline
from app.schemas import TripParams

logger = logging.getLogger(__name__)
router = APIRouter()


async def _run_and_persist(trip_params: TripParams) -> None:
    """Background task: run the LangGraph pipeline and write results to Supabase.

    Failures are caught and recorded on the trip via mark_trip_failed so
    the frontend polling endpoint sees a useful error.
    """
    try:
        final = await run_pipeline(trip_params)

        itinerary = final.get("itinerary")
        if itinerary is None:
            raise RuntimeError("Pipeline produced no itinerary.")

        # Phase 1: write final state only (mid-flight progress lands in Phase 2).
        # research_jobs.phase is an Int in the Prisma schema (1..5) — DO NOT pass strings.
        # See nomad-api/FRONTEND_INTEGRATION_PLAN.md §4.3.
        await supabase_writer.update_research_job(
            trip_params.trip_id,
            status="building",
            phase=5,
            progress=90,
            message="BUILDING YOUR ITINERARY...",
        )
        await supabase_writer.write_itinerary(trip_params.trip_id, itinerary)
        await supabase_writer.mark_trip_ready(
            trip_params.trip_id,
            emoji=itinerary.emoji,
            stats={
                "stats_places": itinerary.stats_places,
                "stats_tips": itinerary.stats_tips,
                "stats_photo_stops": itinerary.stats_photo_stops,
            },
        )
        await supabase_writer.update_research_job(
            trip_params.trip_id,
            status="completed",
            phase=5,
            progress=100,
            message="YOUR ITINERARY IS READY!",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline failed for trip %s", trip_params.trip_id)
        try:
            await supabase_writer.mark_trip_failed(trip_params.trip_id, str(exc))
        except Exception:  # noqa: BLE001
            logger.exception(
                "Also failed to mark trip %s as failed in Supabase", trip_params.trip_id
            )


@router.post(
    "/agent/research",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_internal_secret)],
)
async def trigger_research(
    trip_params: TripParams, background_tasks: BackgroundTasks
) -> JSONResponse:
    """Accept a trip, kick off the pipeline in a background task, return 202."""
    background_tasks.add_task(_run_and_persist, trip_params)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "accepted": True,
            "trip_id": trip_params.trip_id,
        },
    )
