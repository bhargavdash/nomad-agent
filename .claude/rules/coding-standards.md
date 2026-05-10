# Nomad Agent — Coding Standards

> Always-loaded rules for every conversation in this repo.

## Python language

1. **Python 3.12.** Use `from __future__ import annotations` at the top of every module so all annotations are strings (faster import, allows forward refs).
2. **Type hints everywhere.** Use modern syntax: `list[str]`, `dict[str, int]`, `str | None` (not `Optional[str]`, not `List[...]`).
3. **`@dataclass` or Pydantic models** — never bare dicts for structured data crossing module boundaries.
4. **Pydantic v2 syntax.** `model_validate`, `model_dump`, `Field(..., min_length=...)`. Don't use v1's `parse_obj` / `dict()`.
5. **Naming.** `snake_case` for functions/vars/modules, `PascalCase` for classes, `UPPER_SNAKE` for module-level constants and tunables.
6. **Imports.** Standard lib → third-party → `app.*`, separated by blank lines. ruff (isort) enforces this.

## Async

1. **Routes and pipeline nodes are async.** Every function called from a LangGraph node or FastAPI handler must be `async def`.
2. **Wrap sync I/O.** `supabase-py`, `googleapiclient`, and most LangChain provider clients are sync. Wrap them in `await asyncio.to_thread(fn, *args)` when called from async code. Never call them directly from an async function.
3. **No bare `time.sleep` in async code.** Use `await asyncio.sleep(...)`.
4. **Httpx**, not requests, for outbound HTTP from agent code — use the async client.

## Error handling

1. **Research agents must not crash the pipeline.** The public `run_<x>_agent` entry point in [app/agents/](../../app/agents/) wraps the body in `try/except Exception` and returns `[]` on any failure. Log with `logger.exception(...)`. See [app/agents/youtube_shorts.py](../../app/agents/youtube_shorts.py) as the canonical pattern.
2. **Synthesizer is allowed to raise** — its failure is recorded by `mark_trip_failed` in the route handler.
3. **Configuration errors raise `RuntimeError`** with a clear message naming the missing env var. The LLM factory follows this pattern.
4. **Don't swallow exceptions silently.** If you catch `Exception`, log it. Use `# noqa: BLE001` to satisfy ruff for the broad-except in graceful-degradation paths.

## Logging

1. Module-level `logger = logging.getLogger(__name__)`. Don't use `print` in `app/`.
2. Lazy %-formatting: `logger.info("youtube_agent.start query=%r", query)` — not f-strings inside log calls.
3. Use a dotted prefix (`youtube_agent.start`, `pipeline.merge_done`) to make logs greppable.

## LLM calls

1. **Always go through `app.llm.factory.get_llm("<role>")`.** Never instantiate `ChatGroq` / `ChatAnthropic` / etc. directly in agent code.
2. **Prefer `with_structured_output(PydanticModel)`** over manually parsing JSON strings. It works across providers.
3. **Validate the result.** Coerce returned dicts to your Pydantic schema with `Model.model_validate(...)` defensively — some providers return dict, others the model.
4. **Token budget.** Keep prompts compact. Truncate Reddit comment threads, Short descriptions (>400 chars), etc. before sending.

## FastAPI

1. **Routers in `app/routes/`** mounted by `app/main.py`. One router per file.
2. **Validate request bodies as Pydantic models** in the function signature — don't read `await request.json()` manually.
3. **Internal auth** via `Depends(verify_internal_secret)` from [app/auth.py](../../app/auth.py). Every non-health endpoint requires it.
4. **Return `JSONResponse` with explicit status code** for non-200 success (202 for fire-and-forget).
5. **Long work goes in `BackgroundTasks`** — never block the request handler with the LangGraph pipeline.

## Pydantic schemas

1. **`app/schemas.py` is the wire contract** with `nomad-api`. Every change must be mirrored in the corresponding Zod schema on the Node side. Field names are not negotiable.
2. **Nested camelCase fields** (`sortOrder`, `dayNumber`) are intentional — they map directly to Prisma columns on the Node side. Don't "fix" them to snake_case.
3. **Use `Field(..., min_length=N)`** rather than custom `@field_validator` where possible.

## Tests

1. `pytest`. Tests live in `tests/`, mirror `app/` layout where practical.
2. **Pure unit tests** for `signals.py` and `schemas.py` — no API keys, no network.
3. **Live tests** (anything calling YouTube, Reddit, Tavily, Supabase, an LLM) go in files named `test_*_live.py` or scripts under `scripts/`. Don't run by default in CI.
4. `pytest-asyncio` with `asyncio_mode = "auto"` (already configured) — `async def test_...` works without decorators.
5. Use `tests/fixtures/sample_trip.json` rather than constructing `TripParams` from scratch in each test.

## Tooling

1. **ruff** for lint + format: `uv run ruff check .` and `uv run ruff format .`. Line length 100.
2. **mypy** for type checks: `uv run mypy app`. `ignore_missing_imports = true` is set for third-party packages without stubs.
3. **uv** is the package manager. Add deps with `uv add <pkg>` (or `uv add --dev <pkg>` for dev). Don't edit `pyproject.toml` by hand for deps.

## Things to avoid

- **Direct sync calls inside async functions** — wrap with `to_thread`.
- **Hardcoding model names in agent code** — they belong in env / `config.py`.
- **Mutating `PipelineState` in place** in a node — return a `dict` patch instead. LangGraph merges it into state.
- **Catching `BaseException`** — only catch `Exception`.
- **Adding user-facing endpoints** — they go in `nomad-api`, not here.
