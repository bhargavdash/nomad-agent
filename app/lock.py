"""FI-2 — keep locked stops through itinerary rewrite.

Locked rows already persist via nomad-api PATCH. The agent used to insert a
fresh itinerary without reading `Stop.locked`, so a later write would drop
locks and replace those places. Merge is deterministic (no LLM): locked
stops are spliced back onto their day by name, or appended if the new draft
dropped them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas import AIDay, AIItinerary, AIStop, SourceType

_SOURCE: tuple[SourceType, ...] = ("youtube", "reddit", "blog", "maps")


@dataclass(frozen=True)
class LockedStop:
    id: str
    day_number: int
    name: str
    time: str
    ampm: str
    duration: str
    description: str
    source: str
    tags: list[str]
    sort_order: int


def _coerce_source(value: str) -> SourceType:
    if value in _SOURCE:
        return value  # type: ignore[return-value]
    return "maps"


def _as_stop(row: LockedStop) -> AIStop:
    ampm = row.ampm.strip().upper()
    if ampm not in ("AM", "PM"):
        ampm = "AM"
    return AIStop(
        sortOrder=max(1, row.sort_order),
        time=row.time or "9:00",
        ampm=ampm,  # type: ignore[arg-type]
        duration=row.duration or "1h",
        name=row.name,
        description=row.description or row.name,
        source=_coerce_source(row.source),
        tags=row.tags[:4] or ["locked"],
        locked=True,
    )


def apply_locked_stops(itinerary: AIItinerary, locked: list[LockedStop]) -> AIItinerary:
    """Return a copy of `itinerary` with locked stops restored.

    Same-name match on the locked stop's original day wins (fields come from
    the locked row, not the LLM). If the day still exists but the name is
    gone, the locked stop is appended. If that day number is missing, the
    stop is appended to the last remaining day so it is never dropped.
    """
    if not locked:
        return itinerary

    days = [d.model_copy(deep=True) for d in itinerary.days]
    by_number = {d.dayNumber: d for d in days}
    fallback = days[-1] if days else None

    for row in locked:
        day = by_number.get(row.day_number, fallback)
        if day is None:
            continue
        locked_stop = _as_stop(row)
        needle = row.name.strip().lower()
        replaced = False
        for i, stop in enumerate(day.stops):
            if stop.name.strip().lower() == needle:
                day.stops[i] = locked_stop
                replaced = True
                break
        if not replaced:
            day.stops.append(locked_stop)
        for i, stop in enumerate(day.stops, start=1):
            day.stops[i - 1] = stop.model_copy(update={"sortOrder": i})

    # Bypass per-day max_length so a locked append cannot fail closed.
    rebuilt = [
        AIDay.model_construct(
            dayNumber=d.dayNumber,
            city=d.city,
            title=d.title,
            description=d.description,
            highlights=d.highlights,
            stops=d.stops,
        )
        for d in sorted(days, key=lambda d: d.dayNumber)
    ]
    return itinerary.model_copy(update={"days": rebuilt})
