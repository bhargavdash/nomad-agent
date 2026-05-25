"""Unit tests for the Synthesizer agent.

LLM call is mocked everywhere — these tests run with no API keys and no
network. They cover:

  * Title normalisation + cross-source dedup
  * Pace-density → stops-per-day clamping
  * Stats computation (places / tips / photo_stops)
  * Happy path: mocked LLM draft → strict AIItinerary
  * Validation-failure retry then success
  * Repeated failure → skeleton fallback
  * Thin input (< MIN_DISCOVERIES) → skeleton fallback without calling LLM
  * Stop source backfill: LLM-emitted source forced to 'maps' if no matching
    candidate; backfilled to candidate's primary if its choice isn't valid
  * Duration padding: LLM under-delivers days → padded with anchor days
  * Stop padding: LLM under-delivers stops for a day → padded with anchors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.synthesizer import (
    MAX_DISCOVERIES,
    MAX_STOPS_PER_DAY,
    MIN_DISCOVERIES,
    MIN_STOPS_PER_DAY,
    _compute_stats,
    _dedupe_for_prompt,
    _llm_draft_to_itinerary,
    _LLMDay,
    _LLMItineraryDraft,
    _LLMStop,
    _normalize_title,
    _PlaceCandidate,
    _target_stop_counts,
    run_synthesizer,
)
from app.schemas import AIDay, AIStop, ResearchDiscovery, TripParams
from app.signals import extract_signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trip(**overrides: Any) -> TripParams:
    base: dict[str, Any] = dict(
        trip_id="t1",
        user_id="u1",
        destination="Goa, India",
        date_from="2026-12-15",
        date_to="2026-12-22",
        duration_days=7,
        travelers="2",
        vibes=["beaches", "nightlife"],
        accommodation="Boutique Villa",
        pace="Balanced",
        budget="Medium",
        preferences=None,
    )
    base.update(overrides)
    return TripParams(**base)


def _disc(
    *,
    title: str,
    source: str = "youtube",
    body: str = "Some body text describing the place.",
    tags: list[str] | None = None,
    did: str | None = None,
) -> ResearchDiscovery:
    return ResearchDiscovery(
        id=did or f"d-{title}-{source}",
        title=title,
        body=body,
        tags=tags or ["a"],
        source=source,  # type: ignore[arg-type]
    )


def _llm_stop(
    *,
    name: str,
    discovery_title: str = "",
    source: str = "maps",
    time: str = "9:00",
    ampm: str = "AM",
    tags: list[str] | None = None,
) -> _LLMStop:
    return _LLMStop(
        name=name,
        description=f"{name} description.",
        time=time,
        ampm=ampm,  # type: ignore[arg-type]
        duration="1h",
        source=source,  # type: ignore[arg-type]
        tags=tags or ["📍"],
        discovery_title=discovery_title,
    )


def _llm_day(
    *,
    day_number: int,
    city: str = "Goa",
    stops: list[_LLMStop] | None = None,
) -> _LLMDay:
    return _LLMDay(
        dayNumber=day_number,
        city=city,
        title=f"Day {day_number}",
        description=f"Day {day_number} narrative.",
        highlights=["Stop A", "Stop B"],
        stops=stops or [],
    )


# ---------------------------------------------------------------------------
# _normalize_title
# ---------------------------------------------------------------------------


def test_normalize_title_lowercases_and_strips_punctuation() -> None:
    assert _normalize_title("Anjuna Flea Market!") == "anjuna flea market"
    assert _normalize_title("ANJUNA flea-market") == "anjuna flea market"
    assert _normalize_title("  Tito's   Lane  ") == "titos lane"


def test_normalize_title_handles_unicode_and_spaces() -> None:
    # Non-ascii chars are stripped to whitespace then collapsed.
    assert _normalize_title("Café del Mar") == "caf del mar"
    assert _normalize_title("") == ""
    assert _normalize_title("   ") == ""


# ---------------------------------------------------------------------------
# _dedupe_for_prompt
# ---------------------------------------------------------------------------


def test_dedupe_merges_same_place_across_sources() -> None:
    discoveries = [
        _disc(title="Anjuna Flea Market", source="youtube", did="y1"),
        _disc(title="anjuna flea market", source="reddit", did="r1"),
        _disc(title="Anjuna Flea Market", source="blog", did="b1"),
    ]
    cands = _dedupe_for_prompt(discoveries)
    assert len(cands) == 1
    c = cands[0]
    assert c.cross_source is True
    assert set(c.sources) == {"youtube", "reddit", "blog"}
    assert set(c.discovery_ids) == {"y1", "r1", "b1"}


def test_dedupe_keeps_distinct_titles() -> None:
    discoveries = [
        _disc(title="Anjuna Beach", source="youtube"),
        _disc(title="Anjuna Flea Market", source="reddit"),
    ]
    cands = _dedupe_for_prompt(discoveries)
    assert len(cands) == 2
    assert all(not c.cross_source for c in cands)


def test_dedupe_picks_longer_body() -> None:
    discoveries = [
        _disc(title="Vagator", source="youtube", body="short", did="y1"),
        _disc(title="Vagator", source="reddit", body="this is a longer body", did="r1"),
    ]
    cands = _dedupe_for_prompt(discoveries)
    assert len(cands) == 1
    assert cands[0].body == "this is a longer body"


def test_dedupe_drops_empty_titles() -> None:
    discoveries = [
        _disc(title="!!", source="reddit"),  # normalises to empty
        _disc(title="Real Place", source="youtube"),
    ]
    cands = _dedupe_for_prompt(discoveries)
    assert len(cands) == 1
    assert cands[0].title == "Real Place"


# ---------------------------------------------------------------------------
# _target_stop_counts
# ---------------------------------------------------------------------------


def test_target_stop_counts_respects_duration() -> None:
    counts = _target_stop_counts(5, 4)
    assert counts == [4, 4, 4, 4, 4]


def test_target_stop_counts_clamps_below_min() -> None:
    counts = _target_stop_counts(3, 1)  # pace_density way below min
    assert counts == [MIN_STOPS_PER_DAY] * 3


def test_target_stop_counts_clamps_above_max() -> None:
    counts = _target_stop_counts(2, 99)
    assert counts == [MAX_STOPS_PER_DAY] * 2


def test_target_stop_counts_handles_zero_duration() -> None:
    counts = _target_stop_counts(0, 4)
    assert counts == [4]  # min 1 day


# ---------------------------------------------------------------------------
# _compute_stats
# ---------------------------------------------------------------------------


def _build_day(stops: list[AIStop], dn: int = 1) -> AIDay:
    return AIDay(
        dayNumber=dn,
        city="Goa",
        title=f"Day {dn}",
        description="desc",
        highlights=["a", "b"],
        stops=stops,
    )


def _stop(
    *,
    sort_order: int = 1,
    name: str = "Anjuna",
    source: str = "youtube",
    tags: list[str] | None = None,
) -> AIStop:
    return AIStop(
        sortOrder=sort_order,
        time="9:00",
        ampm="AM",
        duration="1h",
        name=name,
        description="A stop",
        source=source,  # type: ignore[arg-type]
        tags=tags or ["📍"],
    )


def test_compute_stats_places_counts_unique_non_maps_stops() -> None:
    # Honest count (BENCHMARK §6 P1 fix): maps-source stops are filler, not
    # researched places, and must NOT contribute to stats_places.
    day = _build_day([
        _stop(sort_order=1, name="Anjuna", source="youtube"),
        _stop(sort_order=2, name="Anjuna", source="youtube"),  # duplicate
        _stop(sort_order=3, name="Baga", source="reddit"),
        _stop(sort_order=4, name="Cultural anchor", source="maps"),  # filler
        _stop(sort_order=5, name="Local breakfast spot", source="maps"),  # filler
    ])
    places, _, _ = _compute_stats([day], discoveries=[])
    assert places == 2  # Anjuna + Baga only — both maps stops excluded


def test_compute_stats_tips_only_counts_tips_used_as_stops() -> None:
    # Honest count (BENCHMARK §6 P2 fix): a tip discovery only contributes to
    # stats_tips if its title is actually referenced by a non-maps stop.
    day = _build_day([
        _stop(sort_order=1, name="Anjuna Flea Market scams", source="reddit"),
        _stop(sort_order=2, name="Old Manali cafés", source="reddit"),
        _stop(sort_order=3, name="Cultural anchor", source="maps"),  # filler
    ])
    discoveries = [
        # Used as stop → counts.
        _disc(title="Anjuna Flea Market scams", tags=["tip"], source="reddit"),
        # Used as stop → counts.
        _disc(title="Old Manali cafés", tags=["recommendation"], source="reddit"),
        # NOT used as a stop → does NOT count.
        _disc(title="Sleeper bus from Delhi", tags=["warning"], source="reddit"),
        # Tag has no tip token → does NOT count.
        _disc(title="Generic cafe", tags=["cafe"], source="blog"),
    ]
    _, tips, _ = _compute_stats([day], discoveries=discoveries)
    assert tips == 2  # only the two tips that surface as stops


def test_compute_stats_photo_stops_ignores_maps_stops() -> None:
    day = _build_day([
        _stop(sort_order=1, name="A", source="youtube", tags=["☕"]),
        _stop(sort_order=2, name="B", source="reddit", tags=["viewpoint"]),
        _stop(sort_order=3, name="C", source="blog", tags=["🍽️"]),
        # A maps anchor with a "viewpoint" tag is still filler — don't count.
        _stop(sort_order=4, name="Sunset viewpoint", source="maps", tags=["viewpoint"]),
    ])
    _, _, photo = _compute_stats([day], discoveries=[])
    assert photo == 2  # youtube + reddit/viewpoint, maps/viewpoint excluded


# ---------------------------------------------------------------------------
# _llm_draft_to_itinerary — mapping & padding
# ---------------------------------------------------------------------------


def _cands_for(titles_with_sources: list[tuple[str, str]]) -> list[_PlaceCandidate]:
    return _dedupe_for_prompt(
        [_disc(title=t, source=s) for t, s in titles_with_sources]
    )


def test_llm_draft_maps_stops_to_candidates_by_title() -> None:
    cands = _cands_for([
        ("Anjuna Beach", "youtube"),
        ("Tito's Lane", "reddit"),
        ("Fontainhas", "blog"),
    ])
    discoveries_by_id = {d.id: d for c in cands for d in []} | {
        c.discovery_ids[0]: ResearchDiscovery(
            id=c.discovery_ids[0],
            title=c.title,
            body=c.body,
            tags=c.tags,
            source=c.sources[0],
        )
        for c in cands
    }
    draft = _LLMItineraryDraft(
        emoji="🌴",
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(
                        name="Anjuna Beach",
                        discovery_title="Anjuna Beach",
                        source="youtube",
                    ),
                    _llm_stop(
                        name="Tito's Lane",
                        discovery_title="Tito's Lane",
                        source="reddit",
                    ),
                    _llm_stop(
                        name="Fontainhas heritage walk",
                        discovery_title="Fontainhas",
                        source="blog",
                    ),
                ],
            ),
        ],
    )
    itin = _llm_draft_to_itinerary(draft, cands, discoveries_by_id, duration_days=1)
    assert itin.emoji == "🌴"
    assert len(itin.days) == 1
    assert [s.source for s in itin.days[0].stops] == ["youtube", "reddit", "blog"]
    # Every stop should have non-empty tags, valid time, and a name.
    for s in itin.days[0].stops:
        assert s.name
        assert s.tags
        assert ":" in s.time


def test_llm_draft_forces_maps_source_when_discovery_title_missing() -> None:
    cands = _cands_for([("Anjuna Beach", "youtube")])
    by_id = {
        c.discovery_ids[0]: ResearchDiscovery(
            id=c.discovery_ids[0],
            title=c.title,
            body=c.body,
            tags=c.tags,
            source=c.sources[0],
        )
        for c in cands
    }
    draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    # LLM claims source="youtube" but didn't reference a real
                    # candidate → should be coerced to "maps".
                    _llm_stop(
                        name="Some random stop",
                        discovery_title="",
                        source="youtube",
                    ),
                    _llm_stop(
                        name="Anjuna Beach",
                        discovery_title="Anjuna Beach",
                        source="youtube",
                    ),
                ],
            ),
        ],
    )
    itin = _llm_draft_to_itinerary(draft, cands, by_id, duration_days=1)
    # Find the "Some random stop" — should be source=maps.
    random_stop = next(s for s in itin.days[0].stops if s.name == "Some random stop")
    assert random_stop.source == "maps"
    # Anjuna stop keeps youtube.
    anjuna = next(s for s in itin.days[0].stops if s.name == "Anjuna Beach")
    assert anjuna.source == "youtube"


def test_llm_draft_pads_days_when_under_target() -> None:
    cands = _cands_for([("Anjuna", "youtube"), ("Baga", "reddit")])
    by_id = {
        c.discovery_ids[0]: ResearchDiscovery(
            id=c.discovery_ids[0],
            title=c.title,
            body=c.body,
            tags=c.tags,
            source=c.sources[0],
        )
        for c in cands
    }
    draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(
                        name="Anjuna", discovery_title="Anjuna", source="youtube"
                    ),
                ],
            ),
        ],
    )
    itin = _llm_draft_to_itinerary(draft, cands, by_id, duration_days=3)
    assert len(itin.days) == 3
    assert [d.dayNumber for d in itin.days] == [1, 2, 3]
    # Each day must satisfy AIDay's min stop count.
    for d in itin.days:
        assert len(d.stops) >= MIN_STOPS_PER_DAY


def test_llm_draft_pads_stops_within_a_day() -> None:
    cands = _cands_for([("Anjuna", "youtube")])
    by_id = {
        c.discovery_ids[0]: ResearchDiscovery(
            id=c.discovery_ids[0],
            title=c.title,
            body=c.body,
            tags=c.tags,
            source=c.sources[0],
        )
        for c in cands
    }
    draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(
                        name="Anjuna", discovery_title="Anjuna", source="youtube"
                    ),
                ],
            ),
        ],
    )
    itin = _llm_draft_to_itinerary(draft, cands, by_id, duration_days=1)
    assert len(itin.days[0].stops) >= MIN_STOPS_PER_DAY


def test_llm_draft_truncates_stops_above_max() -> None:
    cands = _cands_for([(f"Place {i}", "youtube") for i in range(8)])
    by_id = {
        c.discovery_ids[0]: ResearchDiscovery(
            id=c.discovery_ids[0],
            title=c.title,
            body=c.body,
            tags=c.tags,
            source=c.sources[0],
        )
        for c in cands
    }
    draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(
                        name=f"Place {i}",
                        discovery_title=f"Place {i}",
                        source="youtube",
                    )
                    for i in range(8)
                ],
            ),
        ],
    )
    itin = _llm_draft_to_itinerary(draft, cands, by_id, duration_days=1)
    assert len(itin.days[0].stops) == MAX_STOPS_PER_DAY


def test_llm_draft_no_stop_appears_on_two_days() -> None:
    cands = _cands_for([("Anjuna", "youtube"), ("Baga", "reddit"), ("Fontainhas", "blog")])
    by_id = {
        c.discovery_ids[0]: ResearchDiscovery(
            id=c.discovery_ids[0],
            title=c.title,
            body=c.body,
            tags=c.tags,
            source=c.sources[0],
        )
        for c in cands
    }
    # LLM mistakenly emits the same stop on two days — synthesizer should
    # dedupe across days.
    draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(name="Anjuna", discovery_title="Anjuna", source="youtube"),
                    _llm_stop(name="Baga", discovery_title="Baga", source="reddit"),
                    _llm_stop(
                        name="Fontainhas", discovery_title="Fontainhas", source="blog"
                    ),
                ],
            ),
            _llm_day(
                day_number=2,
                stops=[
                    _llm_stop(name="Anjuna", discovery_title="Anjuna", source="youtube"),
                    _llm_stop(name="Baga", discovery_title="Baga", source="reddit"),
                    _llm_stop(
                        name="Fontainhas", discovery_title="Fontainhas", source="blog"
                    ),
                ],
            ),
        ],
    )
    itin = _llm_draft_to_itinerary(draft, cands, by_id, duration_days=2)
    names_seen: set[str] = set()
    for day in itin.days:
        for stop in day.stops:
            assert stop.name.lower() not in names_seen, (
                f"{stop.name!r} appeared on multiple days"
            )
            names_seen.add(stop.name.lower())


def test_llm_draft_output_discoveries_within_bounds() -> None:
    cands = _cands_for([(f"P{i}", "youtube") for i in range(15)])
    by_id = {
        c.discovery_ids[0]: ResearchDiscovery(
            id=c.discovery_ids[0],
            title=c.title,
            body=c.body,
            tags=c.tags,
            source=c.sources[0],
        )
        for c in cands
    }
    draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(name="P0", discovery_title="P0", source="youtube"),
                    _llm_stop(name="P1", discovery_title="P1", source="youtube"),
                    _llm_stop(name="P2", discovery_title="P2", source="youtube"),
                ],
            ),
        ],
    )
    itin = _llm_draft_to_itinerary(draft, cands, by_id, duration_days=1)
    assert MIN_DISCOVERIES <= len(itin.discoveries) <= MAX_DISCOVERIES


# ---------------------------------------------------------------------------
# run_synthesizer — public entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_synthesizer_happy_path_with_mocked_llm() -> None:
    trip = _trip(duration_days=2)
    signals = extract_signals(trip)
    discoveries = [
        _disc(title="Anjuna Beach", source="youtube"),
        _disc(title="Anjuna Flea Market", source="reddit", tags=["tip"]),
        _disc(title="Fontainhas heritage walk", source="blog"),
        _disc(title="Baga Beach", source="youtube"),
    ]
    fake_draft = _LLMItineraryDraft(
        emoji="🌴",
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(
                        name="Anjuna Beach",
                        discovery_title="Anjuna Beach",
                        source="youtube",
                    ),
                    _llm_stop(
                        name="Anjuna Flea Market",
                        discovery_title="Anjuna Flea Market",
                        source="reddit",
                    ),
                    _llm_stop(
                        name="Fontainhas heritage walk",
                        discovery_title="Fontainhas heritage walk",
                        source="blog",
                    ),
                ],
            ),
            _llm_day(
                day_number=2,
                stops=[
                    _llm_stop(
                        name="Baga Beach",
                        discovery_title="Baga Beach",
                        source="youtube",
                    ),
                    _llm_stop(name="Evening stroll", source="maps"),
                    _llm_stop(name="Dinner", source="maps"),
                ],
            ),
        ],
    )
    with patch(
        "app.agents.synthesizer._extract_via_llm",
        AsyncMock(return_value=fake_draft),
    ):
        itin = await run_synthesizer(trip, signals, discoveries)

    assert len(itin.days) == 2
    assert all(len(d.stops) >= MIN_STOPS_PER_DAY for d in itin.days)
    # Sources from the real LLM call should be preserved.
    day1_sources = {s.source for s in itin.days[0].stops}
    assert {"youtube", "reddit", "blog"}.issubset(day1_sources)


@pytest.mark.asyncio
async def test_run_synthesizer_retries_on_validation_error_then_succeeds() -> None:
    trip = _trip(duration_days=1)
    signals = extract_signals(trip)
    discoveries = [
        _disc(title=f"Place {i}", source="youtube") for i in range(3)
    ]
    # First draft is simulated by returning None from _extract_via_llm (the
    # mock's side_effect below); the GOOD second draft is what we map.
    good_draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(
                        name=f"Place {i}",
                        discovery_title=f"Place {i}",
                        source="youtube",
                    )
                    for i in range(3)
                ],
            )
        ],
    )

    # We patch _extract_via_llm directly. Force the FIRST call to return a
    # draft that triggers a ValidationError when mapped; SECOND call returns
    # a good draft. To force the validation error reliably, we simulate
    # the first attempt returning None (simulates LLM failure), and the
    # second returning the good draft.
    mock = AsyncMock(side_effect=[None, good_draft])

    with patch("app.agents.synthesizer._extract_via_llm", mock):
        itin = await run_synthesizer(trip, signals, discoveries)

    assert mock.await_count == 2
    assert len(itin.days) == 1
    assert len(itin.days[0].stops) >= MIN_STOPS_PER_DAY


@pytest.mark.asyncio
async def test_run_synthesizer_falls_back_to_skeleton_on_repeated_failure() -> None:
    trip = _trip(duration_days=2)
    signals = extract_signals(trip)
    discoveries = [
        _disc(title=f"Place {i}", source="youtube") for i in range(4)
    ]
    # Every LLM call returns None.
    mock = AsyncMock(return_value=None)
    with patch("app.agents.synthesizer._extract_via_llm", mock):
        itin = await run_synthesizer(trip, signals, discoveries)

    # Skeleton always produces the requested days and valid stops.
    assert len(itin.days) == 2
    for d in itin.days:
        assert len(d.stops) >= MIN_STOPS_PER_DAY
    # Discovery list still satisfies wire schema.
    assert MIN_DISCOVERIES <= len(itin.discoveries) <= MAX_DISCOVERIES


@pytest.mark.asyncio
async def test_run_synthesizer_skips_llm_when_too_few_discoveries() -> None:
    trip = _trip(duration_days=1)
    signals = extract_signals(trip)
    discoveries = [
        _disc(title="Only Place", source="youtube"),
    ]  # 1 candidate, below MIN_DISCOVERIES
    mock = AsyncMock(return_value=None)
    with patch("app.agents.synthesizer._extract_via_llm", mock):
        itin = await run_synthesizer(trip, signals, discoveries)

    assert mock.await_count == 0  # LLM never called
    assert len(itin.days) == 1
    assert MIN_DISCOVERIES <= len(itin.discoveries) <= MAX_DISCOVERIES


@pytest.mark.asyncio
async def test_run_synthesizer_handles_zero_discoveries() -> None:
    trip = _trip(duration_days=2)
    signals = extract_signals(trip)
    mock = AsyncMock(return_value=None)
    with patch("app.agents.synthesizer._extract_via_llm", mock):
        itin = await run_synthesizer(trip, signals, discoveries=[])

    assert mock.await_count == 0
    assert len(itin.days) == 2
    # Wire schema still satisfied: 3 placeholder discoveries minimum.
    assert MIN_DISCOVERIES <= len(itin.discoveries) <= MAX_DISCOVERIES
    # All stops should be maps anchors.
    for d in itin.days:
        for s in d.stops:
            assert s.source == "maps"


@pytest.mark.asyncio
async def test_run_synthesizer_preserves_source_traceability() -> None:
    """Every stop that's based on a discovery should keep that discovery's source,
    and the discovery itself should appear in itinerary.discoveries."""
    trip = _trip(duration_days=1)
    signals = extract_signals(trip)
    yt = _disc(title="Anjuna Beach", source="youtube", did="yt-1")
    rd = _disc(title="Tito's Lane scams", source="reddit", did="rd-1", tags=["warning"])
    bl = _disc(title="Fontainhas heritage", source="blog", did="bl-1")
    discoveries = [yt, rd, bl]
    draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(
                        name="Anjuna Beach",
                        discovery_title="Anjuna Beach",
                        source="youtube",
                    ),
                    _llm_stop(
                        name="Tito's Lane scams",
                        discovery_title="Tito's Lane scams",
                        source="reddit",
                    ),
                    _llm_stop(
                        name="Fontainhas heritage",
                        discovery_title="Fontainhas heritage",
                        source="blog",
                    ),
                ],
            )
        ],
    )
    with patch(
        "app.agents.synthesizer._extract_via_llm", AsyncMock(return_value=draft)
    ):
        itin = await run_synthesizer(trip, signals, discoveries)

    # Each input discovery should be present in the output discoveries.
    out_ids = {d.id for d in itin.discoveries}
    assert {"yt-1", "rd-1", "bl-1"}.issubset(out_ids)

    # Stop sources match their backing discovery.
    by_name = {s.name: s for s in itin.days[0].stops}
    assert by_name["Anjuna Beach"].source == "youtube"
    assert by_name["Tito's Lane scams"].source == "reddit"
    assert by_name["Fontainhas heritage"].source == "blog"


# ---------------------------------------------------------------------------
# Chronology sort (BENCHMARK §6 P0 fix)
# ---------------------------------------------------------------------------


def test_llm_draft_sorts_stops_chronologically_within_a_day() -> None:
    """LLM may emit stops out of clock order — the synthesizer must re-sort.

    This reproduces the BENCHMARK §6 P0 bug: a day with stops emitted at
    10:00 AM, 5:00 PM, 2:00 PM (the padding preset for index 2) used to
    receive sortOrder 1, 2, 3 in emission order. After the fix, sortOrder
    must follow clock time.
    """
    cands = _cands_for([
        ("Anjuna Flea Market", "youtube"),
        ("Purple Martini", "reddit"),
    ])
    by_id = {
        c.discovery_ids[0]: ResearchDiscovery(
            id=c.discovery_ids[0],
            title=c.title,
            body=c.body,
            tags=c.tags,
            source=c.sources[0],
        )
        for c in cands
    }
    # LLM emits 10:00 AM, 5:00 PM — padding will insert "2:00 PM Cultural
    # anchor" as the 3rd stop (preset index 2). Without the fix, sortOrder
    # would be [1=10AM, 2=5PM, 3=2PM] — chronologically backwards.
    draft = _LLMItineraryDraft(
        days=[
            _llm_day(
                day_number=1,
                stops=[
                    _llm_stop(
                        name="Anjuna Flea Market",
                        discovery_title="Anjuna Flea Market",
                        source="youtube",
                        time="10:00",
                        ampm="AM",
                    ),
                    _llm_stop(
                        name="Purple Martini",
                        discovery_title="Purple Martini",
                        source="reddit",
                        time="5:00",
                        ampm="PM",
                    ),
                ],
            )
        ],
    )
    itin = _llm_draft_to_itinerary(draft, cands, by_id, duration_days=1)
    stops = itin.days[0].stops
    # sortOrder must follow clock time. Convert each stop's time+ampm to
    # minutes and assert non-decreasing across sortOrder.
    from app.agents.synthesizer import _time_to_minutes
    times_in_order = [
        _time_to_minutes(s.time, s.ampm)
        for s in sorted(stops, key=lambda s: s.sortOrder)
    ]
    assert times_in_order == sorted(times_in_order), (
        f"Stops out of chronological order: {[(s.sortOrder, s.time, s.ampm) for s in stops]}"
    )
    # sortOrder must also be a contiguous 1..N sequence.
    assert [s.sortOrder for s in sorted(stops, key=lambda s: s.sortOrder)] == [
        i + 1 for i in range(len(stops))
    ]


def test_time_to_minutes_handles_12hour_edge_cases() -> None:
    from app.agents.synthesizer import _time_to_minutes
    assert _time_to_minutes("12:00", "AM") == 0       # midnight
    assert _time_to_minutes("12:30", "AM") == 30      # 00:30
    assert _time_to_minutes("9:00", "AM") == 9 * 60   # 09:00
    assert _time_to_minutes("12:00", "PM") == 12 * 60  # noon
    assert _time_to_minutes("1:00", "PM") == 13 * 60  # 13:00
    assert _time_to_minutes("11:30", "PM") == 23 * 60 + 30  # 23:30


# ---------------------------------------------------------------------------
# Warning surfacing (BENCHMARK §5 monsoon-signal-squandered fix)
# ---------------------------------------------------------------------------


def test_synth_prompt_contains_warning_surfacing_rule() -> None:
    """Static prompt-contract assertion: the system prompt MUST tell the LLM
    that Day 1's description has to mention a warning when signals.warnings
    is non-empty. This catches accidental prompt regressions.
    """
    from app.agents.synthesizer import _SYNTH_SYSTEM
    lowered = _SYNTH_SYSTEM.lower()
    assert "warning" in lowered
    # The rule fires only when warnings exist — make sure the conditional is
    # documented, not just the word "warning" used in passing.
    assert "day 1" in lowered or "day 1's" in lowered.replace("day 1's", "day 1")


def test_synth_prompt_says_target_is_upper_bound() -> None:
    """Pace-density change: target_per_day is an UPPER BOUND, not a quota.
    Catches accidental regression of the BENCHMARK §7 over-padding behavior.
    """
    from app.agents.synthesizer import _SYNTH_SYSTEM
    lowered = _SYNTH_SYSTEM.lower()
    assert "upper bound" in lowered
    assert "do not invent filler" in lowered or "do not pad" in lowered or (
        "never pad" in lowered
    )


# ---------------------------------------------------------------------------
# WS1 — personalization wiring (preferences / source weights / modifiers)
# ---------------------------------------------------------------------------


def test_build_prompt_includes_traveler_preferences() -> None:
    """trip_params.preferences (the user's free-text) MUST reach the prompt.

    Guards the WS1 fix for the defect where `preferences` was collected but
    never passed to any agent or the synthesizer.
    """
    from app.agents.synthesizer import (
        _build_prompt,
        _dedupe_for_prompt,
        _target_stop_counts,
    )

    trip = _trip(
        preferences="we love local markets and street food, avoid touristy buffets"
    )
    signals = extract_signals(trip)
    cands = _dedupe_for_prompt(
        [_disc(title=f"Place {i}", source="youtube") for i in range(4)]
    )
    counts = _target_stop_counts(trip.duration_days, signals.pace_density)
    _system, user = _build_prompt(trip, signals, cands, counts)
    assert "local markets and street food" in user
    assert "HIGHEST PRIORITY" in user


def test_build_prompt_omits_preferences_block_when_empty() -> None:
    from app.agents.synthesizer import (
        _build_prompt,
        _dedupe_for_prompt,
        _target_stop_counts,
    )

    trip = _trip(preferences=None)
    signals = extract_signals(trip)
    cands = _dedupe_for_prompt(
        [_disc(title=f"Place {i}", source="youtube") for i in range(4)]
    )
    counts = _target_stop_counts(trip.duration_days, signals.pace_density)
    _system, user = _build_prompt(trip, signals, cands, counts)
    assert "HIGHEST PRIORITY" not in user


def test_signal_summary_surfaces_source_weights() -> None:
    """vibe_source_weights MUST surface in the signal summary (WS1)."""
    from app.agents.synthesizer import _format_signal_summary

    trip = _trip(vibes=["budget", "backpacking"])  # reddit-leaning weights
    signals = extract_signals(trip)
    summary = _format_signal_summary(signals)
    assert "Source emphasis" in summary
    assert "reddit" in summary


# ---------------------------------------------------------------------------
# Tier 1 — signals-driven skill overlays + circuit/content prompt
# ---------------------------------------------------------------------------


def test_select_overlays_rajasthan_food_trip() -> None:
    from app.agents.synthesizer import _select_overlays

    trip = _trip(
        destination="Rajasthan, India",
        duration_days=10,
        vibes=["heritage", "local cuisine"],
        preferences="markets for shopping; prefer dhabas and thalis",
    )
    signals = extract_signals(trip)
    overlays = _select_overlays(trip, signals)
    assert "regions/india" in overlays
    assert "trip_shapes/region_multi_city" in overlays
    assert "vibes/food_and_markets" in overlays


def test_select_overlays_single_city_non_food() -> None:
    from app.agents.synthesizer import _select_overlays

    trip = _trip(
        destination="Paris, France",
        duration_days=4,
        vibes=["art", "architecture"],
        preferences=None,
    )
    signals = extract_signals(trip)
    overlays = _select_overlays(trip, signals)
    # Not India, not a multi-city region keyword, no food/shopping vibe.
    assert overlays == []


def test_build_prompt_appends_selected_overlays() -> None:
    from app.agents.synthesizer import (
        _build_prompt,
        _dedupe_for_prompt,
        _target_stop_counts,
    )

    trip = _trip(
        destination="Rajasthan, India",
        duration_days=10,
        vibes=["heritage", "local cuisine"],
    )
    signals = extract_signals(trip)
    cands = _dedupe_for_prompt(
        [_disc(title=f"Place {i}", source="youtube") for i in range(4)]
    )
    counts = _target_stop_counts(trip.duration_days, signals.pace_density)
    system, _user = _build_prompt(trip, signals, cands, counts)
    # Overlay text composed in, AND base prompt format fields resolved.
    assert "Multi-city circuit" in system
    assert "India playbook" in system
    assert "{min_stops}" not in system  # base .format() ran


def test_synth_prompt_has_route_and_content_rules() -> None:
    """Prompt-contract guards for the Tier 1 additions."""
    from app.agents.synthesizer import _SYNTH_SYSTEM

    lowered = _SYNTH_SYSTEM.lower()
    assert "route_summary" in lowered
    assert "plan first" in lowered
    assert "highlights" in lowered
