"""Heuristic itinerary scorer for the eval harness.

Pure, deterministic, token-free: scores a finished `AIItinerary` against a
rubric of objective checks so prompt/agent changes can be regression-tested
across many destinations without eyeballing. The (token-spending) part —
running the pipeline to produce the itineraries — lives in
`scripts/eval_destinations.py`; this module only scores what it's given.

Not an LLM-as-judge: every check here is a deterministic structural/quality
gate (day count, real-place density, currency match, chronology, banned words,
filler dominance, trip-level completeness).
"""

from __future__ import annotations

import math

from app.agents.synthesizer import _is_filler_stop, _time_to_minutes
from app.schemas import AIItinerary, TripParams
from app.signals import TravelSignals

# Brochure words the output should never contain (mirrors the prompt's ban).
_BANNED = (
    "stunning", "breathtaking", "must-visit", "vibrant culture",
    "world-class", "rich history", "something for everyone",
)


def _currency_symbol(currency_hint: str) -> str:
    """'INR (₹)' → '₹'; 'EUR (€)' → '€'; falls back to the whole hint."""
    if "(" in currency_hint and ")" in currency_hint:
        return currency_hint[currency_hint.index("(") + 1 : currency_hint.index(")")].strip()
    return currency_hint.strip()


def score_itinerary(
    itin: AIItinerary, trip: TripParams, signals: TravelSignals
) -> dict:
    """Return {checks: {name: bool}, passed, total, score(0-100)} for an itinerary."""
    days = itin.days
    duration = max(1, trip.duration_days)
    stops = [s for d in days for s in d.stops]
    total_stops = len(stops)
    filler = sum(1 for s in stops if _is_filler_stop(s))

    checks: dict[str, bool] = {}
    checks["day_count_ok"] = len(days) == duration
    checks["has_route_summary"] = bool((itin.route_summary or "").strip())
    checks["enough_real_places"] = itin.stats_places >= math.ceil(duration * 0.7)
    checks["filler_under_40pct"] = total_stops > 0 and (filler / total_stops) < 0.4
    checks["budget_present"] = bool((itin.budget_estimate or "").strip())

    # Currency: when we have a hint, its symbol/code must appear in the budget.
    if signals.currency_hint:
        budget = itin.budget_estimate or ""
        sym = _currency_symbol(signals.currency_hint)
        code = signals.currency_hint.split()[0]
        checks["currency_ok"] = bool(budget) and (
            sym in budget or code.lower() in budget.lower()
        )

    # Chronology: each day's stops are non-decreasing by clock time.
    chrono_ok = True
    for d in days:
        mins = [_time_to_minutes(s.time, s.ampm) for s in d.stops]
        if mins != sorted(mins):
            chrono_ok = False
            break
    checks["chronology_ok"] = chrono_ok

    # No banned brochure words anywhere in the prose.
    text = " ".join(
        f"{d.description} " + " ".join(f"{s.name} {s.description}" for s in d.stops)
        for d in days
    ).lower()
    checks["no_banned_words"] = not any(b in text for b in _BANNED)

    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    return {
        "checks": checks,
        "passed": passed,
        "total": total,
        "score": round(100 * passed / total) if total else 0,
    }
