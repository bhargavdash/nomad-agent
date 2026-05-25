"""SynthesizerAgent — merge all discoveries into the final day-by-day itinerary.

Strategy (matches AI_INTEGRATION_PLAN.md §4.4 + Sprint 2 AI-6):
  • Pure LLM reasoning over collected discoveries — no external tool calls.
  • Receives trip_params, signals, and ALL discoveries from research agents.
  • Pre-LLM Python work:
      - Normalises titles and detects cross-source agreement (same place
        surfaced by 2+ agents → confidence lift, marked in the prompt).
      - Computes target stop count per day from `signals.pace_density`.
      - Builds a dedup-aware view of discoveries for the prompt.
  • LLM produces an `_LLMItineraryDraft` (looser schema; we coerce/validate
    into the strict `AIItinerary` afterward).
  • Hard rule in prompt: every stop MUST reference a discovery title OR be
    explicitly tagged source="maps" as a "standard anchor".
  • Source attribution: each `AIStop.source` is the primary source of the
    discovery it was synthesized from; `AIItinerary.discoveries` keeps the
    individual per-source `ResearchDiscovery` records so a stop can be
    traced back to which agent contributed it.
  • Day assignment first-pass: round-robin with co-location grouping is
    delegated to the LLM via prompt constraints. Geographic clustering /
    time-of-day polish lives in Sprint 3 AI-8.
  • Fallback: if the LLM fails twice or input has < 3 discoveries, we emit
    a deterministic skeleton itinerary so the pipeline never crashes the
    end-to-end script.
  • Default LLM: Anthropic Claude Sonnet (quality matters here).

Failure modes (all handled — synthesizer always returns a valid AIItinerary):
  - LLM call raises → retry once with a stricter prompt → fall back to skeleton.
  - LLM output fails AIItinerary validation → retry once → fall back to skeleton.
  - 0 / 1 / 2 discoveries → skip LLM, emit maps-anchored skeleton directly.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import OrderedDict
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.llm.factory import get_structured_llm
from app.schemas import (
    AIDay,
    AIItinerary,
    AIStop,
    ResearchDiscovery,
    SourceType,
    TripParams,
)
from app.signals import TravelSignals
from app.skills import load_skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# AIItinerary.discoveries is bounded to [3, 12] by the wire schema. We always
# emit between 3 and MAX_DISCOVERIES, padding with maps-anchor placeholders
# if the agents collectively returned too few.
MIN_DISCOVERIES = 3
MAX_DISCOVERIES = 12

# AIDay.stops is bounded to [3, 6]. Minimum of 3 ensures the padding presets
# always fire on thin-research days — city-aware names ("Morning coffee in X")
# are honest placeholders the UI can render rather than an incomplete day.
MIN_STOPS_PER_DAY = 3
MAX_STOPS_PER_DAY = 6

# How many times to retry the LLM on validation failure before falling back.
MAX_LLM_ATTEMPTS = 2

# Source priority used when a place is mentioned by multiple sources and we
# need a single `AIStop.source`. Higher index = higher priority.
_SOURCE_PRIORITY: dict[SourceType, int] = {
    "youtube": 3,
    "blog": 2,
    "reddit": 1,
    "maps": 0,
}

# Reddit `tip` / `warning` discoveries carry these tag tokens (set by the
# Reddit agent — see its `_to_research_discoveries`). Used for stats counting.
_TIP_TAG_TOKENS = {"tip", "warning", "recommendation"}
_PHOTO_TAG_TOKENS = {
    "photo", "photogenic", "view", "viewpoint", "sunset", "sunrise",
    "scenic", "vista", "lookout", "instagram",
}


# ---------------------------------------------------------------------------
# Title normalisation + cross-source dedup
# ---------------------------------------------------------------------------


# Apostrophe variants get DROPPED (not spaced) so e.g. "Tito's Lane"
# merges with "Titos Lane". Hyphens/dashes are NOT dropped — they're
# treated as separators, so "flea-market" merges with "flea market".
_TITLE_INNER_DROP_RE = re.compile(r"['’‘´`]+")
_TITLE_NOISE_RE = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    """Canonical key for cross-source merging.

    Lowercases, drops inner-word punctuation (apostrophes, hyphens),
    spaces other non-alphanumerics, collapses whitespace. Conservative —
    we accept that small phrasing differences (e.g. "Anjuna Beach" vs
    "Anjuna beach in Goa") won't merge. That's fine: the LLM sees the
    raw titles too and can do the soft-match in the prompt.
    """
    lowered = title.lower().strip()
    no_inner = _TITLE_INNER_DROP_RE.sub("", lowered)
    spaced = _TITLE_NOISE_RE.sub(" ", no_inner)
    return _WHITESPACE_RE.sub(" ", spaced).strip()


class _PlaceCandidate(BaseModel):
    """Internal dedup view of a discovery, possibly merged across sources."""

    title: str
    body: str
    sources: list[SourceType]
    tags: list[str]
    discovery_ids: list[str]
    cross_source: bool


def _dedupe_for_prompt(
    discoveries: list[ResearchDiscovery],
) -> list[_PlaceCandidate]:
    """Collapse duplicates by normalised title, preserving multi-source signal.

    Returns an ordered list of `_PlaceCandidate`s. Order = first-seen, which
    naturally follows agent order in the merged input (yt → reddit → blog).
    """
    bucket: "OrderedDict[str, _PlaceCandidate]" = OrderedDict()
    for d in discoveries:
        key = _normalize_title(d.title)
        if not key:
            # Drop garbage titles (would never match a stop name anyway).
            continue
        existing = bucket.get(key)
        if existing is None:
            bucket[key] = _PlaceCandidate(
                title=d.title.strip(),
                body=d.body.strip(),
                sources=[d.source],
                tags=list(d.tags),
                discovery_ids=[d.id],
                cross_source=False,
            )
            continue
        # Merge: extend sources / tags / ids; pick the longer body for context.
        if d.source not in existing.sources:
            existing.sources.append(d.source)
            existing.cross_source = True
        existing.discovery_ids.append(d.id)
        for t in d.tags:
            if t not in existing.tags:
                existing.tags.append(t)
        if len(d.body) > len(existing.body):
            existing.body = d.body.strip()
    return list(bucket.values())


# ---------------------------------------------------------------------------
# Day-shape planning (pure Python — feeds the prompt)
# ---------------------------------------------------------------------------


def _target_stop_counts(duration_days: int, pace_density: int) -> list[int]:
    """Stops-per-day plan respecting AIDay's [3,6] constraint.

    pace_density is the signals-derived target; we clamp into the wire range
    and produce a list of length `duration_days`.
    """
    days = max(1, duration_days)
    target = max(MIN_STOPS_PER_DAY, min(MAX_STOPS_PER_DAY, pace_density))
    return [target] * days


# ---------------------------------------------------------------------------
# LLM-facing internal schema (looser than AIItinerary so smaller models
# survive). We map + validate into AIItinerary afterwards.
# ---------------------------------------------------------------------------


class _LLMStop(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    time: str = Field(default="9:00")
    ampm: Literal["AM", "PM"] = "AM"
    duration: str = Field(default="1h")
    source: SourceType = "maps"
    tags: list[str] = Field(default_factory=list)
    # Which input discovery (by title, soft-matched) this stop is based on.
    # Empty string = standalone maps anchor (allowed only when source="maps").
    discovery_title: str = ""

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


class _LLMDay(BaseModel):
    dayNumber: int = Field(..., ge=1)
    city: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    highlights: list[str] = Field(default_factory=list)
    stops: list[_LLMStop] = Field(default_factory=list)


class _LLMItineraryDraft(BaseModel):
    # Trip-level planning surface (Tier 2). route_summary forces circuit
    # reasoning; the rest carry the GPT-5.5-style overview/transport/stay/budget.
    route_summary: str = Field(default="")
    transport_strategy: str = Field(default="")
    stay_by_city: dict[str, str] = Field(default_factory=dict)
    budget_estimate: str = Field(default="")
    days: list[_LLMDay] = Field(default_factory=list)

    @field_validator("stay_by_city", mode="before")
    @classmethod
    def _coerce_stay(cls, v: Any) -> dict[str, str]:
        # Smaller models sometimes emit a list of {"city","stay"} objects or a
        # string instead of a {city: stay} map. Coerce defensively.
        if isinstance(v, dict):
            return {str(k): str(val) for k, val in v.items() if val}
        if isinstance(v, list):
            out: dict[str, str] = {}
            for item in v:
                if isinstance(item, dict):
                    city = item.get("city") or item.get("name")
                    stay = item.get("stay") or item.get("area") or item.get("suggestion")
                    if city and stay:
                        out[str(city)] = str(stay)
            return out
        return {}


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


_SYNTH_SYSTEM = load_skill("synthesizer")


# --- Signals-driven skill overlays -----------------------------------------
# Deterministic signals (region, trip shape, vibes) pick which domain-playbook
# markdown gets appended to the base synthesizer prompt. This is the pragmatic
# "skills" model for a deterministic pipeline: the *signals* choose the skill,
# not an autonomous agent loop. Each overlay is a file under app/skills/.

# Region/state substrings that imply a multi-city circuit (not a single city).
_MULTI_CITY_REGION_KEYWORDS = (
    "rajasthan", "kerala", "himachal", "uttarakhand", "karnataka",
    "tamil nadu", "tamilnadu", "gujarat", "north india", "south india",
    "golden triangle", "north east", "northeast", "ladakh",
)
# Tokens (matched as substrings against vibes + preferences) that flag a
# food/shopping-forward trip.
_FOOD_SHOP_TOKENS = (
    "food", "foodie", "cuisine", "culinary", "street food",
    "shopping", "market", "bazaar",
)


def _select_overlays(trip_params: TripParams, signals: TravelSignals) -> list[str]:
    """Pick skill-overlay names to append to the synthesizer prompt, by signal."""
    overlays: list[str] = []
    dest = trip_params.destination.lower()
    if signals.region == "india":
        overlays.append("regions/india")
    if trip_params.duration_days >= 4 and any(
        kw in dest for kw in _MULTI_CITY_REGION_KEYWORDS
    ):
        overlays.append("trip_shapes/region_multi_city")
    haystack = (" ".join(trip_params.vibes) + " " + (trip_params.preferences or "")).lower()
    if any(tok in haystack for tok in _FOOD_SHOP_TOKENS):
        overlays.append("vibes/food_and_markets")
    return overlays


def _format_candidates(cands: list[_PlaceCandidate]) -> str:
    """Compact, LLM-friendly rendering of dedup candidates."""
    lines: list[str] = []
    for i, c in enumerate(cands, start=1):
        sources_tag = "+".join(c.sources)
        cross = " ★ CROSS-SOURCE" if c.cross_source else ""
        body = c.body
        if len(body) > 300:
            body = body[:300] + "…"
        lines.append(
            f"[{i}] {c.title}  (sources: {sources_tag}{cross})\n"
            f"    {body}"
        )
    return "\n".join(lines)


def _format_signal_summary(signals: TravelSignals) -> str:
    parts = [
        f"Region: {signals.region}",
        f"Season: {signals.season}",
        f"Crowd level: {signals.crowd_level}",
        f"Budget tier: {signals.budget_tier}",
        f"Pace density: {signals.pace_density} stops/day target",
    ]
    if signals.active_festivals:
        parts.append(f"Active festivals: {', '.join(signals.active_festivals)}")
    if signals.vibe_source_weights:
        weights_str = ", ".join(
            f"{src} {weight:.0%}"
            for src, weight in sorted(
                signals.vibe_source_weights.items(),
                key=lambda kv: kv[1],
                reverse=True,
            )
        )
        parts.append(
            f"Source emphasis (derived from vibes): {weights_str} — when two "
            "candidates compete for the same slot, prefer the one from the "
            "higher-weighted source."
        )
    if signals.query_modifiers:
        parts.append("Interest keywords: " + ", ".join(signals.query_modifiers[:12]))
    if signals.warnings:
        parts.append("Warnings: " + " | ".join(signals.warnings))
    if signals.seasonal_tips:
        parts.append("Seasonal tips: " + " | ".join(signals.seasonal_tips))
    return "\n".join(parts)


def _build_prompt(
    trip_params: TripParams,
    signals: TravelSignals,
    candidates: list[_PlaceCandidate],
    target_counts: list[int],
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt)."""
    target_per_day = target_counts[0] if target_counts else MIN_STOPS_PER_DAY
    system = _SYNTH_SYSTEM.format(
        min_stops=MIN_STOPS_PER_DAY, max_stops=MAX_STOPS_PER_DAY
    )
    # Append signals-selected skill overlays (region/trip-shape/vibe playbooks).
    # Composed AFTER .format() so overlay braces never collide with format fields.
    overlays = _select_overlays(trip_params, signals)
    if overlays:
        system = system + "\n\n" + "\n\n".join(load_skill(name) for name in overlays)
        logger.info("synthesizer.overlays=%s", overlays)
    vibes_str = ", ".join(trip_params.vibes) if trip_params.vibes else "—"
    voice_cues = (
        "=== Voice cues ===\n"
        f"Vibes the traveler picked: {vibes_str}. "
        "Every day's description must hit at least one as a specific detail "
        "(named dish / architect / trail / beach / club), not the bare word.\n"
        f"Budget tier: {trip_params.budget} (= {_BUDGET_HINT.get(trip_params.budget, 'mid-range')}). "
        "Pick cafés, stays, and dining accordingly — never above this tier.\n"
        f"Pace: {trip_params.pace} → aim for ~{target_per_day} stops/day, "
        "emit fewer if the research is thin.\n"
    )
    # The traveler's free-text request is the single most direct expression of
    # intent. It MUST win over generic vibe inference when the two conflict.
    prefs = (trip_params.preferences or "").strip()
    prefs_block = (
        "\n=== Traveler's own words (HIGHEST PRIORITY) ===\n"
        f"{prefs}\n"
        "Honor these explicitly. When they conflict with the generic vibe list "
        "or with a research candidate, the traveler's own words win.\n"
        if prefs
        else ""
    )
    user = (
        f"Destination: {trip_params.destination}\n"
        f"Trip dates: {trip_params.date_from} → {trip_params.date_to}\n"
        f"Duration: {trip_params.duration_days} days "
        f"(produce exactly {len(target_counts)} day entries)\n"
        f"Stops-per-day UPPER BOUND: {target_per_day} "
        f"(hard range {MIN_STOPS_PER_DAY}-{MAX_STOPS_PER_DAY}; emit fewer "
        f"when research is thin — do not invent filler to reach the upper bound)\n"
        f"Travelers: {trip_params.travelers}\n"
        f"Pace: {trip_params.pace}\n"
        f"Vibes: {vibes_str}\n"
        f"Accommodation: {trip_params.accommodation}\n"
        f"{prefs_block}"
        f"\n{voice_cues}"
        f"\n=== Signal summary ===\n{_format_signal_summary(signals)}\n"
        f"\n=== Research candidates ({len(candidates)}) ===\n"
        f"{_format_candidates(candidates)}\n"
        f"\nProduce the day-by-day itinerary now. Reference candidate titles "
        f"in `discovery_title` exactly when used."
    )
    return system, user


# Plain-English description of each budget bucket, surfaced to the LLM in the
# Voice cues block. Kept compact — the synth system prompt rule 15 has the
# full rubric for what each tier means in terms of venues.
_BUDGET_HINT: dict[str, str] = {
    "Low": "shoestring — street food, dhabas, hostels",
    "Medium": "mid-range — cafés, family restaurants, boutique guesthouses",
    "High": "premium — chef-led spots, boutique hotels",
    "Very-High": "luxury — heritage-palace / Michelin-tier dining and stays",
}


# ---------------------------------------------------------------------------
# LLM call with structured output + retry
# ---------------------------------------------------------------------------


async def _extract_via_llm(
    trip_params: TripParams,
    signals: TravelSignals,
    candidates: list[_PlaceCandidate],
    target_counts: list[int],
) -> _LLMItineraryDraft | None:
    """Single LLM call. Returns None on error so caller can retry or fall back."""
    system, user = _build_prompt(trip_params, signals, candidates, target_counts)
    try:
        # Cerebras-235B primary with Groq-70B fallback (see factory). Structured
        # output + provider fallback are composed in get_structured_llm.
        structured = get_structured_llm(
            "synthesizer", _LLMItineraryDraft, method="json_mode"
        )
        messages: list[Any] = [
            SystemMessage(content=system),
            HumanMessage(content=user),
        ]
        logger.info(
            "[LLM] synthesizer → invoking  candidates=%d  days=%d",
            len(candidates),
            len(target_counts),
        )
        result = await structured.ainvoke(messages)
        if not isinstance(result, _LLMItineraryDraft):
            result = _LLMItineraryDraft.model_validate(result)
        logger.info(
            "[LLM] synthesizer → returned  days=%d  route=%r",
            len(result.days) if result.days else 0,
            (result.route_summary or "")[:160],
        )
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("synthesizer.llm_call_failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Mapping LLM draft → strict AIItinerary
# ---------------------------------------------------------------------------


def _resolve_stop_source(
    llm_source: SourceType,
    discovery_title: str,
    candidates_by_norm: dict[str, _PlaceCandidate],
) -> tuple[SourceType, _PlaceCandidate | None]:
    """Map an LLM-emitted stop back to a discovery + canonical source.

    Returns (source, candidate). If the discovery_title doesn't match any
    candidate, the stop is treated as a maps anchor and source is forced
    to 'maps' (regardless of what the LLM said).
    """
    if discovery_title:
        cand = candidates_by_norm.get(_normalize_title(discovery_title))
        if cand is not None:
            # Prefer the LLM's choice if it's one of the candidate's sources;
            # otherwise fall back to the candidate's highest-priority source.
            if llm_source in cand.sources:
                return llm_source, cand
            primary = max(cand.sources, key=lambda s: _SOURCE_PRIORITY.get(s, 0))
            return primary, cand
    # No matching candidate — must be a maps anchor.
    return "maps", None


def _coerce_tags_for_stop(tags: list[str]) -> list[str]:
    """Ensure 1-4 non-empty tags."""
    cleaned = [t.strip() for t in tags if t and t.strip()]
    if not cleaned:
        cleaned = ["📍"]
    return cleaned[:4]


def _coerce_time(time_str: str) -> str:
    """Best-effort coercion to AIStop.time pattern '\\d{1,2}:\\d{2}'."""
    s = time_str.strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        return f"{int(m.group(1))}:{m.group(2)}"
    # Try common alt formats
    m = re.match(r"^(\d{1,2})\.(\d{2})$", s)
    if m:
        return f"{int(m.group(1))}:{m.group(2)}"
    m = re.match(r"^(\d{1,2})$", s)
    if m:
        return f"{int(m.group(1))}:00"
    return "9:00"


def _time_to_minutes(time_str: str, ampm: str) -> int:
    """Convert a coerced AIStop.time + ampm to minutes-since-midnight.

    12-hour clock: 12:30 AM → 30, 12:30 PM → 750, 1:00 PM → 780.
    Used by the day-level chronology sort below — fixes the BENCHMARK §6 P0
    bug where the padding picked "2:00 PM Cultural anchor" after an existing
    5:00 PM stop, then assigned sortOrder by emission order instead of time.
    """
    parts = (time_str or "").split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        h, m = 9, 0
    h = h % 12  # 12 → 0
    if (ampm or "AM").upper() == "PM":
        h += 12
    return h * 60 + m


def _resort_stops_chronologically(stops: list[AIStop]) -> list[AIStop]:
    """Sort a day's stops by clock time, then renumber sortOrder = 1..N.

    Stable sort preserves emission order on ties. Returns a NEW list (the
    inputs' sortOrder fields are replaced). Required because the LLM and the
    padding logic may emit stops in non-chronological order.
    """
    indexed = sorted(
        enumerate(stops),
        key=lambda pair: (_time_to_minutes(pair[1].time, pair[1].ampm), pair[0]),
    )
    out: list[AIStop] = []
    for new_order, (_orig_idx, stop) in enumerate(indexed, start=1):
        out.append(
            AIStop(
                sortOrder=new_order,
                time=stop.time,
                ampm=stop.ampm,
                duration=stop.duration,
                name=stop.name,
                description=stop.description,
                source=stop.source,
                tags=stop.tags,
            )
        )
    return out


def _llm_draft_to_itinerary(
    draft: _LLMItineraryDraft,
    candidates: list[_PlaceCandidate],
    discoveries_by_id: dict[str, ResearchDiscovery],
    duration_days: int,
    signals: TravelSignals | None = None,
) -> AIItinerary:
    """Map (loose) LLM draft → (strict) AIItinerary. Raises ValidationError on fail."""
    candidates_by_norm = {_normalize_title(c.title): c for c in candidates}
    used_discovery_ids: list[str] = []
    seen_stop_names: set[str] = set()

    ai_days: list[AIDay] = []
    target_days = max(1, duration_days)

    # Clip / pad days to exactly target_days
    days_in: list[_LLMDay] = list(draft.days)[:target_days]

    for day_idx, lday in enumerate(days_in, start=1):
        stops_in = list(lday.stops)
        ai_stops: list[AIStop] = []
        sort_order = 1
        for s in stops_in:
            name = s.name.strip()
            if not name or name.lower() in seen_stop_names:
                continue
            seen_stop_names.add(name.lower())

            source, cand = _resolve_stop_source(
                s.source, s.discovery_title, candidates_by_norm
            )
            if cand is not None:
                for did in cand.discovery_ids:
                    if did not in used_discovery_ids:
                        used_discovery_ids.append(did)

            try:
                ai_stops.append(
                    AIStop(
                        sortOrder=sort_order,
                        time=_coerce_time(s.time),
                        ampm=s.ampm,
                        duration=s.duration.strip() or "1h",
                        name=name,
                        description=s.description.strip()
                        or "Recommended stop on this day.",
                        source=source,
                        tags=_coerce_tags_for_stop(s.tags),
                    )
                )
                sort_order += 1
            except ValidationError as e:
                logger.info("synthesizer.stop_dropped name=%r err=%s", name, e)
                continue

            if len(ai_stops) >= MAX_STOPS_PER_DAY:
                break

        # Pad with maps anchors if the LLM under-delivered. Padding presets
        # carry fixed times — chronology sort below re-orders the whole day.
        pad_city = lday.city.strip() or _fallback_city(candidates)
        while len(ai_stops) < MIN_STOPS_PER_DAY:
            ai_stops.append(
                _default_anchor_stop(sort_order, len(ai_stops), pad_city)
            )
            sort_order += 1

        # Sort all stops (LLM-emitted + padding) chronologically and renumber
        # sortOrder. Fixes BENCHMARK §6 P0: padding inserted "2:00 PM" after
        # an existing "5:00 PM" stop produced backwards sortOrder.
        ai_stops = _resort_stops_chronologically(ai_stops)

        highlights = [h.strip() for h in lday.highlights if h and h.strip()]
        if len(highlights) < 2:
            # AIDay.highlights min_length=2; derive from the day's stops.
            highlights = [s.name for s in ai_stops[:2]]
        highlights = highlights[:5]

        ai_days.append(
            AIDay(
                dayNumber=day_idx,
                city=lday.city.strip() or _fallback_city(candidates),
                title=lday.title.strip() or f"Day {day_idx}",
                description=lday.description.strip()
                or "A day of curated stops in this destination.",
                highlights=highlights,
                stops=ai_stops,
            )
        )

    # Pad days if the LLM produced fewer than requested.
    while len(ai_days) < target_days:
        idx = len(ai_days) + 1
        ai_days.append(_default_anchor_day(idx, _fallback_city(candidates)))

    discoveries_out = _select_output_discoveries(
        used_discovery_ids, discoveries_by_id
    )

    stats_places, stats_tips, stats_photo = _compute_stats(ai_days, discoveries_out)

    return AIItinerary(
        stats_places=stats_places,
        stats_tips=stats_tips,
        stats_photo_stops=stats_photo,
        # Trip-level surface: from the LLM draft, except seasonal_tips which are
        # deterministic (from signals) so they're always season-correct.
        route_summary=(draft.route_summary.strip() or None),
        transport_strategy=(draft.transport_strategy.strip() or None),
        seasonal_tips=list(signals.seasonal_tips) if signals else [],
        stay_by_city=dict(draft.stay_by_city),
        budget_estimate=(draft.budget_estimate.strip() or None),
        discoveries=discoveries_out,
        days=ai_days,
    )


def _fallback_city(candidates: list[_PlaceCandidate]) -> str:
    # Simple heuristic: take the first candidate's first word if non-empty.
    for c in candidates:
        first = c.title.split()
        if first:
            return first[0]
    return "Destination"


# ---------------------------------------------------------------------------
# Skeleton fallback (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _default_anchor_stop(sort_order: int, index_in_day: int, city: str) -> AIStop:
    """City-aware maps-anchor stop. Used as padding when the LLM under-delivers.

    Names interpolate the day's city so the UI shows e.g. "Morning coffee in
    Jaipur" rather than the destination-agnostic "Local breakfast spot" filler
    that BENCHMARK §8.1 flagged. The description explicitly signals to the
    user/UI that the stop is a planner placeholder, not a recommendation.

    Generic by design: no per-destination presets — the same six time-slot
    archetypes work for any city in the world. The synthesizer prompt rule 12
    forbids the LLM from emitting these labels, so padding is rare; when it
    fires, the city-name interpolation is the honest signal.
    """
    safe_city = (city or "this city").strip() or "this city"
    presets = [
        ("9:00", "AM", "1h", f"Morning coffee in {safe_city}", "☕"),
        ("11:00", "AM", "2h", f"Old {safe_city} market walk", "🛍️"),
        ("1:00", "PM", "1h30m", f"Lunch in {safe_city}", "🍽️"),
        ("4:00", "PM", "1h30m", f"Sunset point near {safe_city}", "🌅"),
        ("7:30", "PM", "1h30m", f"Dinner in {safe_city}", "🍴"),
        ("9:30", "PM", "1h", f"Evening walk through {safe_city}", "🌙"),
    ]
    time, ampm, duration, name, emoji = presets[index_in_day % len(presets)]
    return AIStop(
        sortOrder=sort_order,
        time=time,
        ampm=ampm,
        duration=duration,
        name=name,
        description=(
            f"Anchor slot suggested by the planner — swap for a specific spot "
            f"in {safe_city} you've already saved."
        ),
        source="maps",
        tags=[emoji],
    )


def _default_anchor_day(day_number: int, city: str) -> AIDay:
    stops = [_default_anchor_stop(i + 1, i, city) for i in range(MIN_STOPS_PER_DAY)]
    return AIDay(
        dayNumber=day_number,
        city=city,
        title=f"Day {day_number} — explore {city}",
        description=(
            "A balanced day of recommended anchors while we gather more "
            "research for this destination."
        ),
        highlights=[stops[0].name, stops[1].name],
        stops=stops,
    )


def _skeleton_itinerary(
    trip_params: TripParams,
    candidates: list[_PlaceCandidate],
    discoveries: list[ResearchDiscovery],
    signals: TravelSignals | None = None,
) -> AIItinerary:
    """Deterministic fallback used when LLM fails or input is too thin.

    Distributes available candidates across days round-robin; pads with
    maps anchors to satisfy AIDay's [3,6] stop constraint.
    """
    duration_days = max(1, trip_params.duration_days)
    city = _fallback_city(candidates) if candidates else trip_params.destination

    # Round-robin candidates into days.
    per_day: list[list[_PlaceCandidate]] = [[] for _ in range(duration_days)]
    for i, c in enumerate(candidates[: duration_days * MAX_STOPS_PER_DAY]):
        per_day[i % duration_days].append(c)

    days: list[AIDay] = []
    for d_idx in range(duration_days):
        day_cands = per_day[d_idx]
        stops: list[AIStop] = []
        sort_order = 1
        for c in day_cands:
            primary = max(c.sources, key=lambda s: _SOURCE_PRIORITY.get(s, 0))
            preset_idx = sort_order - 1
            time, ampm, duration, _name, emoji = [
                ("9:00", "AM", "1h30m", "x", "☕"),
                ("11:00", "AM", "2h", "x", "📍"),
                ("2:00", "PM", "2h", "x", "🏛️"),
                ("5:00", "PM", "1h30m", "x", "🌅"),
                ("7:00", "PM", "2h", "x", "🍽️"),
                ("9:00", "PM", "1h", "x", "🌙"),
            ][preset_idx % 6]
            body = c.body[:160] or "Discovered during research."
            try:
                stops.append(
                    AIStop(
                        sortOrder=sort_order,
                        time=time,
                        ampm=ampm,
                        duration=duration,
                        name=c.title,
                        description=body,
                        source=primary,
                        tags=[emoji],
                    )
                )
                sort_order += 1
            except ValidationError:
                continue
        # Pad
        while len(stops) < MIN_STOPS_PER_DAY:
            stops.append(_default_anchor_stop(sort_order, len(stops), city))
            sort_order += 1
        # Truncate just in case
        stops = stops[:MAX_STOPS_PER_DAY]
        # Chronology sort (same rationale as in the LLM path).
        stops = _resort_stops_chronologically(stops)

        days.append(
            AIDay(
                dayNumber=d_idx + 1,
                city=city,
                title=f"Day {d_idx + 1} — {city}",
                description=(
                    "Skeleton plan generated from research candidates; "
                    "synthesizer LLM was unavailable or input was thin."
                ),
                highlights=[stops[0].name, stops[1].name],
                stops=stops,
            )
        )

    discoveries_out = _pad_discoveries(discoveries)
    stats_places, stats_tips, stats_photo = _compute_stats(days, discoveries_out)

    # Deterministic trip-level surface for the skeleton path (no LLM): a plain
    # city-order route, season-correct tips; transport/stay/budget left empty.
    skeleton_cities: list[str] = []
    for d in days:
        if d.city and d.city not in skeleton_cities:
            skeleton_cities.append(d.city)
    route_summary = " → ".join(skeleton_cities) if skeleton_cities else None

    return AIItinerary(
        stats_places=stats_places,
        stats_tips=stats_tips,
        stats_photo_stops=stats_photo,
        route_summary=route_summary,
        transport_strategy=None,
        seasonal_tips=list(signals.seasonal_tips) if signals else [],
        stay_by_city={},
        budget_estimate=None,
        discoveries=discoveries_out,
        days=days,
    )


# ---------------------------------------------------------------------------
# Discovery selection + stats
# ---------------------------------------------------------------------------


def _select_output_discoveries(
    used_ids: list[str],
    discoveries_by_id: dict[str, ResearchDiscovery],
) -> list[ResearchDiscovery]:
    """Pick discoveries to surface in `AIItinerary.discoveries` (3..12).

    Preference order:
      1. Discoveries actually used by stops (in the order they were used).
      2. Remaining discoveries, in original order, for source diversity.
      3. Pad with maps-tagged placeholders if fewer than 3 exist.
    """
    out: list[ResearchDiscovery] = []
    seen: set[str] = set()
    # Used first
    for did in used_ids:
        d = discoveries_by_id.get(did)
        if d is None or d.id in seen:
            continue
        out.append(d)
        seen.add(d.id)
        if len(out) >= MAX_DISCOVERIES:
            return out
    # Then the rest, original order
    for d in discoveries_by_id.values():
        if d.id in seen:
            continue
        out.append(d)
        seen.add(d.id)
        if len(out) >= MAX_DISCOVERIES:
            return out
    return _pad_discoveries(out)


def _pad_discoveries(
    discoveries: list[ResearchDiscovery],
) -> list[ResearchDiscovery]:
    """Ensure list length ∈ [MIN_DISCOVERIES, MAX_DISCOVERIES]."""
    out = list(discoveries[:MAX_DISCOVERIES])
    while len(out) < MIN_DISCOVERIES:
        out.append(
            ResearchDiscovery(
                id=f"maps-anchor-{uuid.uuid4()}",
                title=f"Standard anchor #{len(out) + 1}",
                body="Planner-suggested anchor (no agent surfaced enough material).",
                tags=["maps"],
                source="maps",
            )
        )
    return out


def _compute_stats(
    days: list[AIDay], discoveries: list[ResearchDiscovery]
) -> tuple[int, int, int]:
    """(stats_places, stats_tips, stats_photo_stops).

    Honest counts (BENCHMARK §6 P1/P2 fix):
    - places: unique stops whose source != "maps". Don't count generic anchors
      like "Cultural anchor" / "Local breakfast spot" — they're filler, not
      places the user actually researched.
    - tips: discoveries with a tip/warning/recommendation tag whose title is
      referenced by at least one stop. Drops the "5 tips badge but 0 tips in
      itinerary" lie from the BENCHMARK run.
    - photo_stops: stops whose source is youtube OR whose tags include a
      photo/view token. Counts only non-maps stops too (maps anchors can't
      be photo discoveries).
    """
    unique_named: set[str] = set()
    referenced_titles: set[str] = set()
    photo = 0
    for day in days:
        for stop in day.stops:
            if stop.source == "maps":
                continue
            unique_named.add(stop.name.lower())
            referenced_titles.add(stop.name.lower())
            tag_text = " ".join(t.lower() for t in stop.tags)
            is_photo = (
                stop.source == "youtube"
                or any(tok in tag_text for tok in _PHOTO_TAG_TOKENS)
            )
            if is_photo:
                photo += 1

    tips = 0
    for d in discoveries:
        tag_tokens = {t.lower() for t in d.tags}
        if not (tag_tokens & _TIP_TAG_TOKENS):
            continue
        # Only count tips actually used by a stop. Titles are short — exact
        # case-insensitive match handles the common case; partial matches catch
        # paraphrased stop names that still preserve the discovery's title.
        title_lc = d.title.lower().strip()
        if not title_lc:
            continue
        if title_lc in referenced_titles or any(
            title_lc in stop_name or stop_name in title_lc
            for stop_name in referenced_titles
        ):
            tips += 1

    return len(unique_named), tips, photo


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_synthesizer(
    trip_params: TripParams,
    signals: TravelSignals,
    discoveries: list[ResearchDiscovery],
) -> AIItinerary:
    """Compose the final itinerary from all discoveries.

    Always returns a valid `AIItinerary`. Errors are caught and degraded to
    the deterministic skeleton — the end-to-end pipeline must not crash on
    LLM failures, schema drift, or thin research input.
    """
    candidates = _dedupe_for_prompt(discoveries)
    discoveries_by_id = {d.id: d for d in discoveries}
    target_counts = _target_stop_counts(
        trip_params.duration_days, signals.pace_density
    )

    logger.info(
        "synthesizer.start destination=%r discoveries=%d candidates=%d days=%d pace=%d",
        trip_params.destination,
        len(discoveries),
        len(candidates),
        len(target_counts),
        signals.pace_density,
    )

    # Thin input → skip the LLM entirely; the prompt-budget cost isn't worth
    # it and the LLM tends to hallucinate when the candidate list is small.
    if len(candidates) < MIN_DISCOVERIES:
        logger.warning(
            "synthesizer: only %d candidates (< %d) — falling back to skeleton",
            len(candidates),
            MIN_DISCOVERIES,
        )
        return _skeleton_itinerary(trip_params, candidates, discoveries, signals)

    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        draft = await _extract_via_llm(
            trip_params, signals, candidates, target_counts
        )
        if draft is None:
            logger.warning(
                "synthesizer: LLM call returned None on attempt %d/%d",
                attempt,
                MAX_LLM_ATTEMPTS,
            )
            continue
        try:
            itinerary = _llm_draft_to_itinerary(
                draft,
                candidates,
                discoveries_by_id,
                trip_params.duration_days,
                signals,
            )
            logger.info(
                "synthesizer.done attempt=%d days=%d stops=%d discoveries=%d",
                attempt,
                len(itinerary.days),
                sum(len(d.stops) for d in itinerary.days),
                len(itinerary.discoveries),
            )
            return itinerary
        except ValidationError as e:
            logger.warning(
                "synthesizer: AIItinerary validation failed on attempt %d/%d: %s",
                attempt,
                MAX_LLM_ATTEMPTS,
                e,
            )

    logger.error(
        "synthesizer: all %d LLM attempts failed — falling back to skeleton",
        MAX_LLM_ATTEMPTS,
    )
    return _skeleton_itinerary(trip_params, candidates, discoveries, signals)
