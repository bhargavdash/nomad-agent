"""POST /agent/refine — targeted itinerary edit (FI-1)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import JSONResponse

from app.agents.refiner import itinerary_from_wire, run_refiner
from app.auth import verify_internal_secret
from app.cache import get_cached_research
from app.db import supabase_writer
from app.pool_filter import filter_pool_for_user
from app.schemas import RefineRequest
from app.signals import extract_signals

logger = logging.getLogger(__name__)
router = APIRouter()


async def _refine_and_persist(body: RefineRequest) -> None:
    trip_id = body.trip_id
    try:
        current = itinerary_from_wire(body.current_itinerary)
    except Exception as exc:  # noqa: BLE001
        logger.exception("refine payload invalid trip=%s", trip_id)
        await supabase_writer.update_research_job(
            trip_id,
            status="failed",
            message=str(exc),
            error=str(exc),
        )
        return

    try:
        await supabase_writer.update_research_job(
            trip_id,
            status="building",
            phase=5,
            progress=40,
            message="REFINING YOUR ITINERARY...",
        )
        signals = extract_signals(body)
        pool = await get_cached_research(body.destination, signals.season) or []
        if pool:
            pool = filter_pool_for_user(pool, signals)
        else:
            pool = list(current.discoveries)

        locked_names = [s.name for d in current.days for s in d.stops if s.locked]
        refined = await run_refiner(
            instruction=body.instruction,
            current=current,
            locked_names=locked_names,
            pool=pool,
        )

        await supabase_writer.update_research_job(
            trip_id,
            status="building",
            phase=5,
            progress=80,
            message="SAVING YOUR UPDATED DAYS...",
        )
        city_images = await supabase_writer.fetch_city_images(trip_id)
        await supabase_writer.write_itinerary(trip_id, refined, city_images)
        await supabase_writer.mark_trip_ready(
            trip_id,
            stats={
                "stats_places": refined.stats_places,
                "stats_tips": refined.stats_tips,
                "stats_photo_stops": refined.stats_photo_stops,
            },
        )
        try:
            await supabase_writer.update_trip_overview(trip_id, refined)
        except Exception:  # noqa: BLE001
            logger.warning("update_trip_overview failed after refine trip=%s", trip_id, exc_info=True)

        await supabase_writer.update_research_job(
            trip_id,
            status="completed",
            phase=5,
            progress=100,
            message="ITINERARY UPDATED",
            stats_places=refined.stats_places,
            stats_tips=refined.stats_tips,
            stats_photo_stops=refined.stats_photo_stops,
        )
        logger.info("refine complete trip=%s", trip_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("refine failed trip=%s", trip_id)
        # Leave trip.status as ready so the previous itinerary stays visible.
        await supabase_writer.update_research_job(
            trip_id,
            status="failed",
            message=str(exc),
            error=str(exc),
        )


@router.post(
    "/agent/refine",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_internal_secret)],
)
async def trigger_refine(body: RefineRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    background_tasks.add_task(_refine_and_persist, body)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"accepted": True, "trip_id": body.trip_id, "mode": "refine"},
    )
