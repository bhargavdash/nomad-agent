"""POST /agent/trending-refresh — fire-and-forget seasonal trending refresh.

Called by nomad-api when its cache for the current season key is missing.
Runs `generate_trending()` (a single Cerebras Qwen call) and upserts the
result into Supabase's `trending_cache` table. Returns 202 immediately —
the Node API serves the previously-cached row in the meantime.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents.trending import (
    Season,
    current_season,
    current_season_key,
    generate_trending,
)
from app.auth import verify_internal_secret
from app.db import supabase_writer
from app.images import resolve_and_store_trending_images

logger = logging.getLogger(__name__)
router = APIRouter()


class TrendingRefreshRequest(BaseModel):
    """Optional request body. If omitted, the agent computes the current key."""

    season_key: str | None = None
    season: Season | None = None
    year: int | None = None


async def _run_refresh(season: Season, year: int, season_key: str) -> None:
    try:
        logger.info(
            "[trending] refresh start  season=%s  year=%s  key=%s",
            season,
            year,
            season_key,
        )
        payload = await generate_trending(season=season, year=year)
        # Resolve + self-host a photo per destination before caching so the Node
        # /trending endpoint serves stored URLs (no lazy on-read hydration ->
        # no race). Best-effort: failures leave imageUrl None (FE fallback).
        await resolve_and_store_trending_images(payload)
        await supabase_writer.write_trending(season_key, payload)
        logger.info("[trending] refresh done   key=%s", season_key)
    except Exception:  # noqa: BLE001
        logger.exception("[trending] refresh failed key=%s", season_key)


@router.post(
    "/agent/trending-refresh",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_internal_secret)],
)
async def trigger_trending_refresh(
    req: TrendingRefreshRequest | None,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    body = req or TrendingRefreshRequest()

    if body.season_key:
        # Allow caller-controlled key (matches what Node sent). Parse it
        # back into (season, year) so the agent and the cache row agree.
        try:
            season_str, year_str = body.season_key.rsplit("-", 1)
            season: Season = season_str  # type: ignore[assignment]
            year = int(year_str)
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Invalid season_key. Expected 'season-year'."},
            )
        if season not in ("summer", "monsoon", "post-monsoon", "winter"):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": f"Unknown season in season_key: {season!r}."},
            )
        season_key = body.season_key
    else:
        season = body.season or current_season()
        year = body.year or _current_year()
        season_key = f"{season}-{year}"

    background_tasks.add_task(_run_refresh, season, year, season_key)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "accepted": True,
            "season_key": season_key,
        },
    )


def _current_year() -> int:
    # Tiny helper so the route module stays free of datetime imports at the
    # top level (keeps the auto-completion focused on the trending agent).
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).year


__all__ = [
    "router",
    "current_season",
    "current_season_key",
]
