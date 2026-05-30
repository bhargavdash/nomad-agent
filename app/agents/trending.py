"""SA-8 trending destinations agent.

Single, cheap LLM call that returns 10 India + 10 international destinations
for Indian travellers, with a one-line blurb each. Driven by a season key
(`{season}-{year}`) so the refresh cadence is ~4 calls per year. The call
runs against Cerebras Qwen by default (see `Settings.LLM_TRENDING_*`).

Not a LangGraph node — this isn't part of the research pipeline. It's a
plain `async def` invoked by `POST /agent/trending-refresh`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from app.llm.factory import get_structured_llm
from app.schemas import TrendingPayload

logger = logging.getLogger(__name__)

Season = Literal["summer", "monsoon", "post-monsoon", "winter"]


def current_season(now: datetime | None = None) -> Season:
    """India-aware season. Mirror of nomad-api/src/services/season.service.ts.

    Months use UTC to match the Node helper (which calls `getUTCMonth`),
    so a refresh fired by Node and one fired by curl land on the same key.
    """
    now = now or datetime.now(timezone.utc)
    m = now.month  # 1 = Jan
    if 3 <= m <= 5:
        return "summer"
    if 6 <= m <= 9:
        return "monsoon"
    if 10 <= m <= 11:
        return "post-monsoon"
    return "winter"


def current_season_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{current_season(now)}-{now.year}"


_PROMPT_TEMPLATE = """\
You are a travel-trend analyst for Indian travellers.

Generate exactly 10 trending destinations within India and exactly 10
trending international destinations for travellers based in India, for
{season} {year}.

Rules:
- Reflect realistic seasonal preferences for Indian travellers:
  * winter favours warmer or snowy destinations depending on intent
  * summer favours hill stations, Europe, and cool-climate spots
  * monsoon favours offbeat / lush / non-coastal places, avoids most beaches
  * post-monsoon is peak travel — broad mix
- India list: only places inside India (states / cities / regions).
- International list: places outside India that are popular and accessible
  for an Indian passport holder (visa-on-arrival, e-visa, or easy visa).
- Each blurb: <= 15 words, evocative, no marketing fluff, no exclamation marks.
- duration: a short range like "5-7 days" or "3-4 days".
- vibe_tags: 1 to 3 short single-word lowercase tags (e.g. "beach",
  "heritage", "mountains", "food", "nightlife", "adventure", "spiritual",
  "luxury", "offbeat", "family", "romance", "wellness", "wildlife").
- Do not repeat a destination across the two lists.

Output strictly matches the TrendingPayload schema. Set:
  season = "{season}"
  year   = {year}
"""


async def generate_trending(
    season: Season | None = None,
    year: int | None = None,
) -> TrendingPayload:
    """Run the LLM call and return a validated TrendingPayload."""
    now = datetime.now(timezone.utc)
    season = season or current_season(now)
    year = year or now.year

    prompt = _PROMPT_TEMPLATE.format(season=season, year=year)
    logger.info("[trending] generating payload for season=%s year=%s", season, year)

    llm = get_structured_llm("trending", TrendingPayload)
    payload: TrendingPayload = await llm.ainvoke(prompt)

    # The schema enforces lengths/types; nothing extra to validate here.
    return payload
