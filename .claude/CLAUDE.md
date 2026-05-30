# Nomad Agent — Claude Code Development Guide

## What this service is

Python + FastAPI + LangGraph service that owns the **agentic AI pipeline** for the Nomad travel itinerary app. Companion to the Node `nomad-api` service: Node owns auth/CRUD/polling; this service owns multi-agent research + synthesis. Both write to the **same Supabase Postgres database**.

The architectural source of truth is [`AI_INTEGRATION_PLAN.md`](../../nomad-api/AI_INTEGRATION_PLAN.md) in the `nomad-api` repo. Read it before any non-trivial change.

## Tech stack

| Layer | Choice |
|-------|--------|
| Runtime | Python 3.12 (managed by [`uv`](https://docs.astral.sh/uv/)) |
| Framework | FastAPI + Uvicorn |
| Agent orchestration | LangGraph (state machine, parallel + sequential nodes) |
| LLM clients | LangChain (`langchain-groq`, `-anthropic`, `-openai`, `-google-genai`) — chosen per-role via env |
| Validation | Pydantic v2 (mirrors Zod schemas on the Node side) |
| DB | Supabase Python client (`supabase-py`) — sync API wrapped in `asyncio.to_thread` |
| Tools | YouTube Data API v3, Reddit JSON, Tavily Search |
| Tests | pytest + pytest-asyncio |
| Lint/type | ruff, mypy |

## Pipeline topology

```
POST /agent/research
        │
        ▼
   signal_node            ← pure Python, no LLM (app/signals.py)
        │
   ┌────┼────┬────┐
   ▼    ▼    ▼
 youtube reddit google     ← parallel research agents
   │    │    │
   └────┼────┘
        ▼
   merge_node              ← concatenates ResearchDiscovery[]
        ▼
   synthesizer_node        ← flagship LLM (Anthropic by default)
        ▼
   supabase_writer         ← itinerary_days + stops + research_jobs
```

Compiled in [app/graph/pipeline.py](app/graph/pipeline.py); triggered by [app/routes/research.py](app/routes/research.py) as a FastAPI `BackgroundTask` after returning **202 Accepted**.

## Project structure

```
nomad-agent/
├── pyproject.toml               # uv-managed, deps + dev tools
├── .env.example
├── README.md
│
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   ├── config.py                # pydantic-settings env loader
│   ├── auth.py                  # INTERNAL_AGENT_SECRET check
│   ├── schemas.py               # Pydantic models — CONTRACT with Node side
│   ├── signals.py               # SignalExtractor — pure Python
│   ├── llm/
│   │   └── factory.py           # get_llm("<role>") — model-agnostic dispatch
│   ├── agents/
│   │   ├── youtube_shorts.py    # <60s Shorts → discoveries
│   │   ├── reddit.py            # Reddit JSON → discoveries
│   │   ├── google_blog.py       # Tavily web search → discoveries
│   │   └── synthesizer.py       # discoveries → AIItinerary
│   ├── tools/                   # API wrappers (youtube, reddit, tavily)
│   ├── graph/pipeline.py        # LangGraph state machine
│   ├── db/supabase_writer.py    # Supabase writes — column names matter
│   └── routes/research.py       # POST /agent/research, GET /agent/health
│
├── scripts/
│   ├── run_agent_locally.py        # full pipeline, no FastAPI
│   ├── run_youtube_agent_locally.py
│   └── test_supabase_writer_live.py
│
└── tests/
    ├── test_signals.py
    ├── test_schemas.py
    ├── test_youtube_agent.py
    ├── test_youtube_tool.py
    └── test_supabase_writer.py
```

## Key decisions

- **Polyglot split.** This service has **zero auth/CRUD logic**. Don't add user-facing endpoints — they belong in `nomad-api`.
- **Model-agnostic by design.** Every LLM call goes through [app/llm/factory.py](app/llm/factory.py). Per-role provider/model is set via env (`LLM_<ROLE>_PROVIDER` / `_MODEL`). Never instantiate `ChatGroq`/`ChatAnthropic` directly in agent code.
- **Signals before LLMs.** [app/signals.py](app/signals.py) is the personalization layer — pure Python, deterministic. Agents read `signals.query_modifiers`, `signals.crowd_level`, etc. to shape prompts and queries.
- **Graceful degradation.** Each research agent's public entry point catches all exceptions and returns `[]` on failure. The synthesizer must produce *some* itinerary even if 0–2 research agents return empty. Never let one agent's failure crash the pipeline.
- **Supabase column contract.** [app/db/supabase_writer.py](app/db/supabase_writer.py) writes raw column names that must match `nomad-api`'s Prisma schema (snake_case). Mismatches silently 400.
- **Internal auth.** `INTERNAL_AGENT_SECRET` shared with Node. Verified by [app/auth.py](app/auth.py). This is a private, fire-and-forget endpoint — not user-facing.
- **Sync supabase-py in async context.** Wrap every supabase call in `asyncio.to_thread`. Don't block the event loop.

## Reference priority

[`AI_INTEGRATION_PLAN.md`](../../nomad-api/AI_INTEGRATION_PLAN.md) > `rules/db-contract.md` > `rules/agent-architecture.md` > this file > `rules/coding-standards.md`.

## Rule file map

| File | Scope | Contents |
|------|-------|----------|
| [rules/coding-standards.md](rules/coding-standards.md) | All Python files | Type hints, async, ruff/mypy, error handling |
| [rules/agent-architecture.md](rules/agent-architecture.md) | `app/agents/**`, `app/graph/**`, `app/signals.py`, `app/llm/**` | Pipeline contract, LLM factory rules, signal extraction |
| [rules/db-contract.md](rules/db-contract.md) | `app/db/**`, `app/schemas.py`, `app/routes/**` | Column names, Node↔Python wire format, internal auth |

## Auto-select rules — no prompt needed

These fire automatically from hooks or from explicit recognition. Claude must act on them without being asked:

| Trigger | Auto action |
|---------|-------------|
| Any codebase question + `graphify-out/graph.json` exists | Run `graphify query "<question>"` before grepping — never grep raw files first |
| Question about LangGraph, FastAPI, Pydantic v2, supabase-py, httpx | Use **context7 MCP** (`resolve_library_id` + `get_library_docs`) for up-to-date docs |
| Edited `app/schemas.py` or `app/db/supabase_writer.py` | Invoke **schema-sync-checker** subagent before task is done |
| Edited `app/agents/**`, `app/graph/**`, or `app/db/**` | Invoke **pipeline-reviewer** subagent before task is done |
| Edited `app/agents/synthesizer.py` or `app/signals.py` | Run `uv run pytest tests/test_eval.py -v`; score must be >= 85 |
| User says "add a new source / agent / API" | Load **add-research-agent** skill |
| User says "swap / change the LLM / model / provider" | Load **swap-llm-provider** skill |
| User says "add a tool / wrapper / API client" | Load **add-tool** skill |
| User says "eval / quality / score / regression" | Load **run-eval** skill |
| End of any response where files were edited | `graphify update .` runs automatically via Stop hook |

## Agents

| Agent | Auto-trigger (hook) | Manual invocation |
|-------|--------------------|--------------------|
| [pipeline-reviewer](agents/pipeline-reviewer.md) | Edit in `app/agents/`, `app/graph/`, `app/db/` | After adding or modifying any pipeline component |
| [schema-sync-checker](agents/schema-sync-checker.md) | Edit to `schemas.py` or `supabase_writer.py` | Before any PR touching the schema |

## Skills

| File | Trigger phrase | What it does |
|------|----------------|--------------|
| [skills/add-research-agent.md](skills/add-research-agent.md) | "add a new source/agent" | Full 9-step checklist for a 4th+ research agent |
| [skills/swap-llm-provider.md](skills/swap-llm-provider.md) | "swap/change model or provider" | One-line env swap or full provider addition guide |
| [skills/add-tool.md](skills/add-tool.md) | "add a tool/wrapper/API client" | Scaffold new external API tool following httpx/async pattern |
| [skills/run-eval.md](skills/run-eval.md) | "eval/quality/score/regression" | Run itinerary eval harness, interpret score >= 85 threshold |

## Scripts

| Command | What it does |
|---------|---|
| `uv sync` | Install deps into managed venv |
| `uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` | Start FastAPI dev server |
| `uv run python scripts/run_agent_locally.py` | Run full pipeline against `tests/fixtures/sample_trip.json`, no HTTP |
| `uv run python scripts/run_youtube_agent_locally.py` | Run only the YouTube agent (fast iteration) |
| `uv run pytest` | Full test suite |
| `uv run pytest tests/test_schemas.py tests/test_signals.py` | Pure-Python tests only (no API keys needed) |
| `uv run pytest tests/test_eval.py -v` | Eval harness — score must be >= 85 after synthesizer/signals changes |
| `uv run ruff check .` | Lint |
| `uv run mypy app` | Type-check |

## Required env vars

Boot:
- `INTERNAL_AGENT_SECRET` (must match value Node sends)
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`

Per-agent (only when that agent runs):
- `YOUTUBE_API_KEY` — YouTube agent
- `TAVILY_API_KEY` — GoogleBlog agent
- `GROQ_API_KEY` — default for research agents
- `ANTHROPIC_API_KEY` — default for synthesizer

Optional: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `TOGETHER_API_KEY`, `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true`.

## Knowledge graph (graphify)

This project has a knowledge graph at `graphify-out/` with god nodes, community structure, and cross-file relationships.

- For codebase questions, first run `graphify query "<question>"` when `graphify-out/graph.json` exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than `GRAPH_REPORT.md` or raw grep output.
- If `graphify-out/wiki/index.md` exists, use it for broad navigation instead of raw source browsing.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost). This also runs automatically via the Stop hook.
