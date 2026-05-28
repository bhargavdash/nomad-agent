---
name: run-eval
description: Run the itinerary eval harness against test fixtures and report quality scores. Use after any synthesizer prompt change, signals update, or scoring rubric change to catch regressions before merging.
---

## What this does

Exercises `app/eval.py`'s `score_itinerary()` — a deterministic, token-free rubric that scores a finished `AIItinerary` on structural and quality checks. No LLM calls, no API keys needed for the quick mode.

## Quick mode (free — no API calls)

```bash
uv run pytest tests/test_eval.py -v
```

Scores itineraries built from fixtures in `tests/fixtures/` against the full rubric. Runs in seconds.

## Full mode (costs ~1 Anthropic call per destination)

```bash
uv run python scripts/eval_destinations.py
```

Runs the complete pipeline for each destination, then scores the output. Use sparingly — only when you need to validate a prompt change end-to-end across multiple destinations.

## Rubric reference

| Check | What a failure means |
|---|---|
| `day_count_ok` | Synthesizer returned wrong number of days vs. `trip_params.duration_days` |
| `has_route_summary` | `itinerary.route_summary` is empty or whitespace |
| `enough_real_places` | `stats_places` < 70% of duration — too many filler stops |
| `filler_under_40pct` | More than 40% of stops are generic `source="maps"` anchors |
| `budget_present` | `budget_estimate` field is empty |
| `currency_ok` | Budget text doesn't contain the destination's currency symbol/code |
| `chronology_ok` | A day's stops are not in non-decreasing time order |
| `no_banned_words` | Brochure-speak slipped through (see `_BANNED` in `app/eval.py`) |

## Interpreting results

- **Score ≥ 85** — healthy; merge is safe
- **Score 70–84** — investigate the failing checks; likely a prompt regression
- **Score < 70** — do not merge; the synthesizer output has structural problems

## When to run

- After any edit to `app/agents/synthesizer.py` (prompt, structured output schema, stop filtering)
- After any edit to `app/signals.py` (pace_density, vibe_source_weights, query_modifiers)
- After adding a new destination fixture to `tests/fixtures/`
- Before opening a PR that touches the LLM pipeline
