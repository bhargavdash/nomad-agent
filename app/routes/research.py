"""POST /agent/research — fire-and-forget pipeline trigger."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import JSONResponse

from app.auth import verify_internal_secret
from app.db import supabase_writer
from app.graph.pipeline import run_pipeline
from app.images import resolve_and_store_itinerary_images
from app.schemas import TripParams

logger = logging.getLogger(__name__)
router = APIRouter()


# Fake-sequential phase progression driven while the (real-parallel) LangGraph
# pipeline runs underneath. See FRONTEND_INTEGRATION_PLAN.md §4.2 + §8 Phase 2.
#
# Each step: (delay_seconds_before_write, phase, progress, message, stats_places,
# stats_tips, stats_photo_stops). The first step writes immediately (delay=0).
# After the last step, the pacer idles until cancelled — so if the pipeline
# overruns, the FE sees a stable "phase 4 / 85%" rather than the bar reverting.
_PACER_STEPS: list[tuple[int, int, int, str, int, int, int]] = [
    (0, 1, 15, "SCANNING YOUTUBE VLOGS...", 4, 6, 2),
    (12, 2, 35, "READING REDDIT THREADS...", 12, 14, 5),
    (16, 3, 55, "PARSING GOOGLE RESULTS...", 22, 22, 10),
    (17, 4, 75, "ANALYZING TRAVEL BLOGS...", 30, 28, 14),
    (15, 4, 85, "ANALYZING TRAVEL BLOGS...", 35, 30, 16),
]


async def _progress_pacer(trip_id: str) -> None:
    """Drive monotonic phase/progress writes for the FE polling animation.

    Cancelled cleanly by `_run_and_persist` once the pipeline returns; that's
    the expected exit path. CancelledError is re-raised so the awaiter knows
    the task ended cleanly. Any other exception during a write is swallowed
    so a transient Supabase blip can't kill the pipeline run.
    """
    try:
        for delay, phase, progress, message, places, tips, photo in _PACER_STEPS:
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await supabase_writer.update_research_job(
                    trip_id,
                    status="researching",
                    phase=phase,
                    progress=progress,
                    message=message,
                    stats_places=places,
                    stats_tips=tips,
                    stats_photo_stops=photo,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Pacer write failed for trip %s at phase %s — continuing.",
                    trip_id,
                    phase,
                    exc_info=True,
                )
        # All steps done — idle until cancelled so the FE keeps seeing
        # the last reported state. Sleep for a very long time; the awaiter
        # will cancel us within seconds anyway.
        await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise


async def _cancel_pacer(task: asyncio.Task[None]) -> None:
    """Cancel the pacer cleanly so no 'Task was destroyed but pending!' warning."""
    if task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _run_and_persist(trip_params: TripParams) -> None:
    """Background task: run the LangGraph pipeline and write results to Supabase.

    Flow (FRONTEND_INTEGRATION_PLAN.md §8 Phase 2):
      1. Launch the progress pacer as a background task — it writes phase
         1→4 on a schedule for the FE's polling animation while the pipeline
         actually runs in parallel underneath.
      2. Run the pipeline. The merge_node writes a first chunk of
         discoveries mid-flight so the FE's "LIVE DISCOVERY" card animates.
      3. On success: cancel pacer, write phase=5/building, write the full
         discoveries list, write itinerary, mark trip ready, write
         completed status with real stats.
      4. On failure: cancel pacer, mark trip + research_job failed.
    """
    t0 = time.perf_counter()
    logger.info(
        "━━━ PIPELINE START  trip_id=%s  dest=%r  days=%s  vibes=%r  pace=%s  prefs=%r ━━━",
        trip_params.trip_id,
        trip_params.destination,
        trip_params.duration_days,
        trip_params.vibes,
        trip_params.pace,
        trip_params.preferences or "",
    )
    pacer_task = asyncio.create_task(_progress_pacer(trip_params.trip_id))

    try:
        final = await run_pipeline(trip_params)
        pipeline_elapsed = time.perf_counter() - t0
        logger.info("━━━ PIPELINE DONE  elapsed=%.1fs ━━━", pipeline_elapsed)

        itinerary = final.get("itinerary")
        if itinerary is None:
            raise RuntimeError("Pipeline produced no itinerary.")

        await _cancel_pacer(pacer_task)

        # Phase 5 — building. research_jobs.phase is Int in Prisma (1..5).
        logger.info("[DB] phase=5 status=building → writing to Supabase")
        await supabase_writer.update_research_job(
            trip_params.trip_id,
            status="building",
            phase=5,
            progress=90,
            message="BUILDING YOUR ITINERARY...",
        )

        # Final full-list write so the FE swaps to the last discovery card.
        # merge_node already wrote a partial list mid-flight.
        all_discoveries = final.get("all_discoveries") or []
        if all_discoveries:
            logger.info("[DB] writing final discoveries count=%d", len(all_discoveries))
            await supabase_writer.write_discoveries(trip_params.trip_id, all_discoveries)

        logger.info(
            "[DB] writing itinerary  days=%d  stops_places=%d  stops_tips=%d  stops_photo=%d",
            len(itinerary.days),
            itinerary.stats_places,
            itinerary.stats_tips,
            itinerary.stats_photo_stops,
        )
        # Resolve + self-host hero/per-city imagery (best-effort — never blocks
        # completion). Runs in the "building" phase so it's covered by the FE's
        # existing progress animation. The agent is the SOLE image writer; Node
        # and web only read these URLs, so there is no read/write race.
        hero_image_url: str | None = None
        city_images: dict[str, str | None] = {}
        try:
            hero_image_url, city_images = await resolve_and_store_itinerary_images(
                trip_params.trip_id, trip_params.destination, itinerary
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "image resolution failed for trip %s — continuing; FE shows fallbacks.",
                trip_params.trip_id,
                exc_info=True,
            )

        await supabase_writer.write_itinerary(trip_params.trip_id, itinerary, city_images)
        await supabase_writer.mark_trip_ready(
            trip_params.trip_id,
            stats={
                "stats_places": itinerary.stats_places,
                "stats_tips": itinerary.stats_tips,
                "stats_photo_stops": itinerary.stats_photo_stops,
            },
            hero_image_url=hero_image_url,
        )

        # Trip-level planning surface (Tier 2). Isolated + best-effort: if the
        # new columns aren't migrated yet, this logs and continues — the trip is
        # already marked ready above, so a missing column never blocks completion.
        try:
            await supabase_writer.update_trip_overview(trip_params.trip_id, itinerary)
        except Exception:  # noqa: BLE001
            logger.warning(
                "update_trip_overview failed for trip %s (columns migrated?) — "
                "continuing; trip is already ready.",
                trip_params.trip_id,
                exc_info=True,
            )

        # Final completion. Stats on research_jobs feed the FE polling
        # response (`routes/research.ts`) which is camelCased to
        # `stats.{places,tips,photoStops}` for the FE.
        total_elapsed = time.perf_counter() - t0
        logger.info(
            "[DB] status=completed  places=%d  tips=%d  photo_stops=%d  total_elapsed=%.1fs",
            itinerary.stats_places,
            itinerary.stats_tips,
            itinerary.stats_photo_stops,
            total_elapsed,
        )
        await supabase_writer.update_research_job(
            trip_params.trip_id,
            status="completed",
            phase=5,
            progress=100,
            message="YOUR ITINERARY IS READY!",
            stats_places=itinerary.stats_places,
            stats_tips=itinerary.stats_tips,
            stats_photo_stops=itinerary.stats_photo_stops,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - t0
        logger.exception(
            "━━━ PIPELINE FAILED  elapsed=%.1fs  trip=%s ━━━",
            elapsed,
            trip_params.trip_id,
        )
        await _cancel_pacer(pacer_task)
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
