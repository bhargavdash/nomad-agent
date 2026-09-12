"""FI-1 targeted itinerary refine — one structured LLM call, no re-research.

The current itinerary is conversation state. Locked stops are echoed in the
prompt and re-applied deterministically by `write_itinerary` (FI-2). On LLM
failure we keep the current itinerary rather than wiping the trip.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.llm.factory import get_structured_llm
from app.schemas import AIDay, AIItinerary, AIStop, ResearchDiscovery, SourceType

log = logging.getLogger(__name__)

_SOURCES: tuple[SourceType, ...] = ("youtube", "reddit", "blog", "maps")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


class _RefineStop(BaseModel):
    sortOrder: int = 1
    time: str = "9:00"
    ampm: Literal["AM", "PM"] = "AM"
    duration: str = "1h"
    name: str = Field(..., min_length=1)
    description: str = ""
    source: SourceType = "maps"
    tags: list[str] = Field(default_factory=list)
    locked: bool = False

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        if isinstance(v, list):
            return [str(t).strip() for t in v if str(t).strip()]
        return []


class _RefineDay(BaseModel):
    dayNumber: int = Field(..., ge=1)
    city: str = "Destination"
    title: str = "Day"
    description: str = ""
    highlights: list[str] = Field(default_factory=list)
    stops: list[_RefineStop] = Field(default_factory=list)


class _RefineDraft(BaseModel):
    route_summary: str = ""
    transport_strategy: str = ""
    stay_by_city: dict[str, str] = Field(default_factory=dict)
    budget_estimate: str = ""
    currency_advice: str = ""
    days: list[_RefineDay] = Field(default_factory=list)


def _coerce_source(value: Any) -> SourceType:
    if value in _SOURCES:
        return value  # type: ignore[return-value]
    return "maps"


def _coerce_time(value: Any) -> str:
    s = str(value or "").strip()
    if _TIME_RE.match(s):
        h, m = s.split(":")
        return f"{int(h)}:{m}"
    return "9:00"


def _coerce_ampm(value: Any) -> Literal["AM", "PM"]:
    return "PM" if str(value or "").strip().upper() == "PM" else "AM"


def _coerce_tags(tags: list[str]) -> list[str]:
    cleaned = [t.strip() for t in tags if t and str(t).strip()]
    return (cleaned or ["place"])[:4]


def _pad_discoveries(raw: list[Any]) -> list[ResearchDiscovery]:
    out: list[ResearchDiscovery] = []
    for item in raw:
        try:
            if isinstance(item, ResearchDiscovery):
                out.append(item)
            elif isinstance(item, dict):
                out.append(ResearchDiscovery.model_validate(item))
        except ValidationError:
            continue
        if len(out) >= 12:
            break
    while len(out) < 3:
        n = len(out) + 1
        out.append(
            ResearchDiscovery(
                id=f"maps-anchor-{uuid.uuid4()}",
                title=f"Standard anchor #{n}",
                body="Planner-suggested anchor used so a refine can round-trip.",
                tags=["maps"],
                source="maps",
            )
        )
    return out


def _stop_from_wire(raw: dict[str, Any], sort_order: int) -> AIStop | None:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    desc = str(raw.get("description") or name).strip() or name
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    try:
        return AIStop(
            sortOrder=max(1, int(raw.get("sortOrder") or raw.get("sort_order") or sort_order)),
            time=_coerce_time(raw.get("time")),
            ampm=_coerce_ampm(raw.get("ampm")),
            duration=str(raw.get("duration") or "1h").strip() or "1h",
            name=name,
            description=desc,
            source=_coerce_source(raw.get("source")),
            tags=_coerce_tags([str(t) for t in tags]),
            locked=bool(raw.get("locked")),
        )
    except ValidationError:
        return None


def _pad_day_stops(day: AIDay) -> AIDay:
    stops = list(day.stops)
    city = day.city or "this city"
    while len(stops) < 2:
        idx = len(stops) + 1
        stops.append(
            AIStop(
                sortOrder=idx,
                time="12:00" if idx == 2 else "9:00",
                ampm="PM" if idx == 2 else "AM",
                duration="1h",
                name=f"Open slot in {city}",
                description=f"Placeholder so this day stays valid after a refine in {city}.",
                source="maps",
                tags=["place"],
            )
        )
    highlights = [h for h in day.highlights if h and str(h).strip()]
    if len(highlights) < 2:
        highlights = [s.name for s in stops[:2]]
    return AIDay.model_construct(
        dayNumber=day.dayNumber,
        city=day.city,
        title=day.title,
        description=day.description,
        highlights=highlights[:5],
        stops=stops[:6],
    )


def itinerary_from_wire(raw: dict[str, Any]) -> AIItinerary:
    """Coerce a Node/Prisma-shaped itinerary into AIItinerary."""
    days_out: list[AIDay] = []
    for i, day in enumerate(raw.get("days") or [], start=1):
        if not isinstance(day, dict):
            continue
        stops: list[AIStop] = []
        for j, stop in enumerate(day.get("stops") or [], start=1):
            if not isinstance(stop, dict):
                continue
            parsed = _stop_from_wire(stop, j)
            if parsed is not None:
                stops.append(parsed)
        city = str(day.get("city") or raw.get("destination") or "Destination")
        title = str(day.get("title") or f"Day {i}")
        desc = str(day.get("description") or title)
        highlights = [str(h) for h in (day.get("highlights") or []) if h]
        built = _pad_day_stops(
            AIDay.model_construct(
                dayNumber=int(day.get("dayNumber") or day.get("day_number") or i),
                city=city,
                title=title,
                description=desc,
                highlights=highlights,
                stops=stops,
            )
        )
        days_out.append(built)

    if not days_out:
        raise ValueError("itinerary has no days to refine")

    discoveries = _pad_discoveries(list(raw.get("discoveries") or []))
    stay = raw.get("stay_by_city") or raw.get("stayByCity") or {}
    if not isinstance(stay, dict):
        stay = {}

    return AIItinerary.model_construct(
        stats_places=int(raw.get("stats_places") or raw.get("statsPlaces") or 0),
        stats_tips=int(raw.get("stats_tips") or raw.get("statsTips") or 0),
        stats_photo_stops=int(raw.get("stats_photo_stops") or raw.get("statsPhotoStops") or 0),
        route_summary=raw.get("route_summary") or raw.get("routeSummary"),
        transport_strategy=raw.get("transport_strategy") or raw.get("transportStrategy"),
        seasonal_tips=list(raw.get("seasonal_tips") or raw.get("seasonalTips") or []),
        stay_by_city={str(k): str(v) for k, v in stay.items() if v},
        budget_estimate=raw.get("budget_estimate") or raw.get("budgetEstimate"),
        currency_advice=raw.get("currency_advice") or raw.get("currencyAdvice"),
        discoveries=discoveries,
        days=days_out,
    )


def _ai_stop_from_refine(stop: _RefineStop, sort_order: int, locked: bool) -> AIStop:
    return AIStop(
        sortOrder=sort_order,
        time=_coerce_time(stop.time),
        ampm=_coerce_ampm(stop.ampm),
        duration=(stop.duration or "1h").strip() or "1h",
        name=stop.name.strip(),
        description=(stop.description or stop.name).strip() or stop.name,
        source=_coerce_source(stop.source),
        tags=_coerce_tags(stop.tags),
        locked=locked,
    )


def merge_refine_draft(original: AIItinerary, draft: _RefineDraft) -> AIItinerary:
    """Apply changed days from the LLM; unreturned days stay as-is."""
    locked_names = {
        s.name.strip().lower() for d in original.days for s in d.stops if s.locked
    }
    original_by_num = {d.dayNumber: d for d in original.days}
    changed: dict[int, AIDay] = {}

    for lday in draft.days:
        stops: list[AIStop] = []
        seen: set[str] = set()
        for stop in lday.stops:
            name = stop.name.strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            stops.append(
                _ai_stop_from_refine(
                    stop,
                    len(stops) + 1,
                    locked=stop.locked or key in locked_names,
                )
            )
            if len(stops) >= 6:
                break
        city = (lday.city or "").strip() or original_by_num.get(
            lday.dayNumber, original.days[0]
        ).city
        title = (lday.title or "").strip() or f"Day {lday.dayNumber}"
        desc = (lday.description or "").strip() or title
        highlights = [h.strip() for h in lday.highlights if h and h.strip()]
        changed[lday.dayNumber] = _pad_day_stops(
            AIDay.model_construct(
                dayNumber=lday.dayNumber,
                city=city,
                title=title,
                description=desc,
                highlights=highlights,
                stops=stops,
            )
        )

    merged_days = []
    for day in original.days:
        merged_days.append(changed.get(day.dayNumber, day))

    route = (draft.route_summary or "").strip()
    transport = (draft.transport_strategy or "").strip()
    budget = (draft.budget_estimate or "").strip()
    currency = (draft.currency_advice or "").strip()
    stay = dict(draft.stay_by_city) or dict(original.stay_by_city)

    return original.model_copy(
        update={
            "days": merged_days,
            "route_summary": route or original.route_summary,
            "transport_strategy": transport or original.transport_strategy,
            "stay_by_city": stay,
            "budget_estimate": budget or original.budget_estimate,
            "currency_advice": currency or original.currency_advice,
        }
    )


def _itinerary_json(itinerary: AIItinerary) -> str:
    return itinerary.model_dump_json(indent=2)


async def run_refiner(
    *,
    instruction: str,
    current: AIItinerary,
    locked_names: list[str],
    pool: list[ResearchDiscovery],
) -> AIItinerary:
    lock_note = ""
    if locked_names:
        lock_note = (
            "LOCKED STOPS — echo these unchanged (same day/time/name), never remove: "
            + ", ".join(locked_names)
            + ".\n"
        )
    pool_blob = [
        {"title": d.title, "body": d.body[:400], "tags": d.tags, "source": d.source}
        for d in pool[:15]
    ]
    prompt = f"""Apply the traveler's instruction to this existing itinerary.
Do not invent a brand-new trip. Keep the destination, day count, and untouched
days stable. Prefer alternatives from the discovery pool when swapping places.
{lock_note}
You MAY return only the days you changed (same dayNumber). Days you omit stay
as they are. Each returned day needs 2-6 stops.

INSTRUCTION:
{instruction}

CURRENT ITINERARY:
{_itinerary_json(current)}

DISCOVERY POOL (candidates for swaps; may be empty):
{pool_blob}

Return JSON with days (and optional route_summary, transport_strategy,
stay_by_city, budget_estimate, currency_advice).
"""
    try:
        structured = get_structured_llm("refiner", _RefineDraft, method="json_mode")
        result = await structured.ainvoke(
            [
                SystemMessage(
                    content=(
                        "You refine travel itineraries. Output JSON only. "
                        "Never drop or rename locked stops."
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        if not isinstance(result, _RefineDraft):
            result = _RefineDraft.model_validate(result)
        if not result.days:
            log.warning("refiner returned no days — keeping current itinerary")
            return current
        return merge_refine_draft(current, result)
    except Exception:
        log.exception("refine LLM failed; keeping current itinerary")
        return current
