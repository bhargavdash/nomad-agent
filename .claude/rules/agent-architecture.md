# Nomad Agent — Pipeline & Agent Architecture

> Loaded when working on `app/agents/**`, `app/graph/**`, `app/signals.py`, or `app/llm/**`.

## The pipeline contract

Defined in [app/graph/pipeline.py](../../app/graph/pipeline.py).

```
START → signals → {youtube, reddit, google} (parallel) → merge → synthesizer → END
```

State is a `TypedDict` (`PipelineState`) — all fields optional so each node only writes the slice it owns:

| Field | Owner | Type |
|---|---|---|
| `trip_params` | caller (route) | `TripParams` |
| `signals` | `signal_node` | `TravelSignals` |
| `yt_discoveries` | `youtube_node` | `list[ResearchDiscovery]` |
| `reddit_discoveries` | `reddit_node` | `list[ResearchDiscovery]` |
| `google_discoveries` | `google_node` | `list[ResearchDiscovery]` |
| `all_discoveries` | `merge_node` | `list[ResearchDiscovery]` |
| `itinerary` | `synthesizer_node` | `AIItinerary` |
| `error` | any node (rare) | `str \| None` |

Nodes return a **dict patch**, not the whole state. LangGraph merges it.

## Adding / modifying nodes

1. **Parallel-safe writes only.** The 3 research nodes run concurrently, so each writes to a *different* state key. Never have two parallel nodes write to the same key (LangGraph will raise without a custom reducer).
2. **Sequential nodes** (signals → research, merge → synth) can read everything written by their predecessors.
3. **Compile eagerly.** `graph = build_graph()` is at module top-level so misconfigurations fail at import, not at first request.

## Research agent contract

Every research agent exports one async function:

```python
async def run_<source>_agent(
    trip_params: TripParams,
    signals: TravelSignals,
) -> list[ResearchDiscovery]: ...
```

Rules:
1. **Never raise.** Wrap the body in `try/except Exception` and return `[]` on failure. Synthesizer must be able to run with 0–N agents returning empty.
2. **Build queries from signals.** Read `signals.query_modifiers`, `signals.crowd_level`, `signals.active_festivals`, etc. Don't hardcode keywords; the personalization layer is `signals.py`.
3. **Tag discoveries with the right `source`.** `"youtube"` | `"reddit"` | `"blog"` | `"maps"` (Literal in `app/schemas.py`). Adding a new source means updating the `SourceType` literal *and* the corresponding Zod schema in `nomad-api`.
4. **IDs are UUIDs** — `str(uuid.uuid4())`. Don't reuse IDs across runs.
5. **Quality > quantity.** Cap discoveries at ~8 per agent. The synthesizer cannot fit more than ~12 total into an itinerary, and longer prompts dilute the synthesis.

## Synthesizer contract

[app/agents/synthesizer.py](../../app/agents/synthesizer.py) is the only agent allowed to:
- Use a flagship-class LLM (default: `claude-sonnet-4-6` via `LLM_SYNTH_PROVIDER=anthropic`).
- Raise on hard failure (its failure marks the whole trip failed).
- Return `AIItinerary` (full Pydantic schema, validated).

Synthesizer prompt rules (enforced in the prompt, not in code):
- Most stops should reference a `discovery`. Stops with no backing discovery use `source: "maps"`.
- `pace_density` from signals controls stops/day (3 / 4 / 5).
- `vibe_source_weights` from signals biases which sources dominate.
- Day count = `trip_params.duration_days`.

## LLM factory rules

[app/llm/factory.py](../../app/llm/factory.py) is the **only** place that instantiates LLM clients.

1. Roles are: `"youtube_agent"`, `"reddit_agent"`, `"google_agent"`, `"synthesizer"`.
2. Adding a role = add a row to `_resolve_role`'s mapping + add `LLM_<ROLE>_PROVIDER` / `_MODEL` to `app/config.py`.
3. Adding a provider = add a branch handling its `langchain-*` import + API-key check. Follow the existing pattern: `if not settings.<KEY>: raise RuntimeError(...)`.
4. **Never put provider-specific behavior in agent code.** If Anthropic needs a specific param, set it inside the factory.

## Signals (the personalization layer)

[app/signals.py](../../app/signals.py) is **pure Python, no LLM calls, deterministic.**

`TravelSignals` fields agents read:
- `season` — affects activity choice (monsoon → indoor)
- `active_festivals` — bias prompts toward festival-aware picks
- `crowd_level` — `"low" | "moderate" | "peak"`
- `weather_hint`
- `budget_tier` — derived from `trip_params.budget` ($/$$/$$$/$$$$)
- `pace_density` — stops/day, derived from `trip_params.pace`
- `vibe_source_weights` — `{"reddit": 0.5, "youtube": 0.4, "blog": 0.1}`
- `query_modifiers` — extra keywords for search queries (most useful field for agents)

When extending `signals.py`:
- Always pure functions of `TripParams`. No I/O, no LLM, no time-of-day branching.
- Add unit tests in `tests/test_signals.py` for any new destination × season rule.
- The plan calls out a future YAML-based lookup table (`destination × month → signals`). When the if/elif chain crosses ~15 destinations, refactor to data-driven.

## Graceful degradation matrix

| Failure | Behavior |
|---|---|
| `YOUTUBE_API_KEY` missing | YouTube agent returns `[]`, pipeline continues |
| YouTube quota 403 | Same — caught and `[]` |
| Reddit blocks UA | Reddit agent returns `[]` |
| Tavily quota | Google agent returns `[]` |
| All 3 research agents return `[]` | Synthesizer still runs, fills with `source: "maps"` standard anchors |
| Synthesizer fails | Route handler calls `mark_trip_failed(trip_id, error)` |
| Supabase write fails after success | Logged; trip remains in `building` — manual recovery |

## When to add a new research agent

See [skills/add-research-agent.md](../skills/add-research-agent.md). Quick checklist:
1. New `app/tools/<source>.py` wrapper.
2. New `app/agents/<source>.py` following the YouTube agent pattern.
3. New role in the LLM factory + config.
4. New node in `pipeline.py`, wired parallel to the existing 3.
5. Add `<source>_discoveries` to `PipelineState` and extend `merge_node`.
6. Add `"<source>"` to `SourceType` literal — and mirror in `nomad-api`'s Zod schema.
