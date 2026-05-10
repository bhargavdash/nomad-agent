# Skill: Add a New Research Agent

Use this when adding a 4th+ research source (e.g. `instagram_reels`, `tiktok`, `substack`, `tripadvisor`) alongside the existing YouTube / Reddit / GoogleBlog agents.

## Architecture recap

A research agent is a node in the LangGraph pipeline that:
1. Fetches raw data from an external API (a "tool")
2. Filters / aggregates the raw data
3. Asks an LLM (chosen via `get_llm("<role>")`) to extract a list of `ResearchDiscovery`
4. Returns the list — **never raises** — into pipeline state

Use [app/agents/youtube_shorts.py](../../app/agents/youtube_shorts.py) as the canonical reference. It demonstrates every required pattern.

## Steps

### 1. Add the source to the literal

In [app/schemas.py](../../app/schemas.py):

```python
SourceType = Literal["youtube", "reddit", "blog", "maps", "instagram"]
```

Then mirror in `nomad-api`'s Zod schema (and DB check constraint if present).

### 2. Build the tool wrapper

`app/tools/<source>.py`:
- One async function exporting the data fetch (`async def search_<source>(query, max_results) -> list[<RawItem>]`)
- A small dataclass / Pydantic model for the raw item (`InstagramReel`, etc.)
- API key check raising `RuntimeError` if env var is missing
- Wrap any sync SDK calls in `asyncio.to_thread`
- Use `httpx.AsyncClient` for raw HTTP

### 3. Add the LLM role

[app/config.py](../../app/config.py):

```python
LLM_INSTAGRAM_PROVIDER: str = "groq"
LLM_INSTAGRAM_MODEL: str = "llama-3.3-70b-versatile"
```

[app/llm/factory.py](../../app/llm/factory.py) — extend `_resolve_role`:

```python
"instagram_agent": (settings.LLM_INSTAGRAM_PROVIDER, settings.LLM_INSTAGRAM_MODEL),
```

### 4. Build the agent

`app/agents/<source>.py`. Required public entry:

```python
async def run_<source>_agent(
    trip_params: TripParams,
    signals: TravelSignals,
) -> list[ResearchDiscovery]:
    try:
        # ... fetch via tool, filter, build prompt, LLM call, validate
        return discoveries
    except RuntimeError as e:
        logger.error("<source>_agent config error: %s", e)
        return []
    except Exception as e:  # noqa: BLE001
        logger.exception("<source>_agent unexpected failure: %s", e)
        return []
```

Conventions to copy from `youtube_shorts.py`:
- Module-level tunables (`MAX_RESULTS`, `MIN_QUALITY`, `MAX_DISCOVERIES_RETURNED`)
- An internal `_ExtractedDiscovery` Pydantic model + `_ExtractionResult` wrapper
- `_build_query`, `_filter_quality`, `_format_for_prompt`, `_build_user_prompt` helpers
- `llm.with_structured_output(_ExtractionResult)` for typed extraction
- Convert `_ExtractedDiscovery` → `ResearchDiscovery` with a fresh UUID and `source="<source>"`

### 5. Wire the LangGraph node

[app/graph/pipeline.py](../../app/graph/pipeline.py):

```python
class PipelineState(TypedDict, total=False):
    # ...
    instagram_discoveries: list[ResearchDiscovery]

async def instagram_node(state):
    discoveries = await run_instagram_agent(state["trip_params"], state["signals"])
    return {"instagram_discoveries": discoveries}

async def merge_node(state):
    merged = []
    merged.extend(state.get("yt_discoveries", []) or [])
    merged.extend(state.get("reddit_discoveries", []) or [])
    merged.extend(state.get("google_discoveries", []) or [])
    merged.extend(state.get("instagram_discoveries", []) or [])  # ← new
    return {"all_discoveries": merged}

def build_graph():
    # ...
    g.add_node("instagram", instagram_node)
    g.add_edge("signals", "instagram")
    g.add_edge("instagram", "merge")
```

The graph re-compiles on next import; `graph.compile()` will fail loudly if the topology is malformed.

### 6. Update signals if helpful

If the new source has a unique strength (e.g. Instagram is great for visual highlights), update `signals.vibe_source_weights` in [app/signals.py](../../app/signals.py) to bias certain trips toward it.

### 7. Tests

- `tests/test_<source>_tool.py` — mock the HTTP/SDK calls, check filter logic
- `tests/test_<source>_agent.py` — mock the LLM via `monkeypatch` on `get_llm`; assert the agent gracefully returns `[]` when the tool raises
- Live test as `tests/test_<source>_tool_live.py` if desired (don't run in CI)

### 8. Local dry-run script

Add `scripts/run_<source>_agent_locally.py` mirroring `scripts/run_youtube_agent_locally.py`. This lets you iterate on the prompt without the full pipeline.

### 9. Docs

- Add the env var to `.env.example` and the env-var section in [README.md](../../README.md) and [.claude/CLAUDE.md](../CLAUDE.md).
- If the new source needs a quirky API setup (auth flow, app registration), put a short note in the agent module docstring.

## Checklist

- [ ] `SourceType` literal extended (here AND in `nomad-api` Zod)
- [ ] `app/tools/<source>.py` written with API-key guard
- [ ] `LLM_<SOURCE>_*` env vars added to `app/config.py` and `.env.example`
- [ ] LLM factory `_resolve_role` mapping extended
- [ ] `app/agents/<source>.py` follows the `youtube_shorts.py` template
- [ ] Public `run_<source>_agent` catches `Exception` → returns `[]`
- [ ] Pipeline node wired parallel to existing 3, with new state key
- [ ] `merge_node` extended to include the new discoveries
- [ ] Tests added (at least the tool filter + agent graceful-degradation cases)
- [ ] Local dry-run script added
- [ ] README + CLAUDE.md updated
