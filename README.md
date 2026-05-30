# nomad-agent

The agentic AI service for the **Nomad** travel itinerary app. Python + FastAPI + LangGraph. Companion to the Node `nomad-api` service: Node owns auth, CRUD, and polling; this service owns the multi-agent research + synthesis pipeline. Both write to the same Supabase database.

Architecture deep-dives live in:
- [`SYSTEM_DESIGN.md`](./SYSTEM_DESIGN.md) — architecture and design decisions
- [`IMPROVEMENT_PLAN.md`](./IMPROVEMENT_PLAN.md) — quality workstream roadmap (Milestones A–D)
- [`SIGNOFF.md`](./SIGNOFF.md) — completed refinement summary

---

## What this is

- **FastAPI** service exposing `POST /agent/research` (called by Node) and `GET /agent/health`.
- **LangGraph** pipeline running **4 research agents in parallel** (YouTubeShortsAgent, YouTubeLongFormAgent, RedditAgent, GoogleBlogAgent) followed by a SynthesizerAgent.
- **Signal extraction** layer (`app/signals.py`) — pure-Python derivation of season / festival / crowd / budget / vibe weights from trip params. Runs before any LLM and shapes every search query.
- **Skills system** (`app/skills/`) — prompts as versioned markdown files loaded at runtime. Prompt changes don't require code deploys.
- **Redis L1 cache** (`app/cache.py`) — destination research cache + geocode cache. A repeat-destination hit skips all 4 research agents (~13 LLM + ~50 API calls), relieving the free-tier quota.
- **Geo-routing layer** (`app/geo/`) — OSM Nominatim geocoding + haversine distances + nearest-neighbor city ordering + sunrise/sunset. Fed to the synthesizer as a geo brief to prevent route backtracking.
- **Model-agnostic.** Each agent's provider/model is set per-role via env vars (`LLM_YOUTUBE_PROVIDER`, `LLM_SYNTH_PROVIDER`, etc.). Synthesizer uses Cerebras Qwen-3-235B (free 1M tok/day) with Groq Llama-3.3-70B fallback. Research agents default to Groq 70B.

---

## Pipeline topology

```
POST /agent/research
        │ 202 Accepted (pipeline runs as BackgroundTask)
        ▼
   signal_node              ← pure Python, no LLM (app/signals.py)
        │
   research_gate            ← L1 Redis cache check (app/cache.py)
        │
   ┌────┼────┬────┐
   ▼    ▼    ▼    ▼
 yt   yt-lf redd  blog    ← 4 parallel research agents (Groq 70B)
   │    │    │    │
   └────┼────┴────┘
        ▼
   merge_node              ← concat + anchor seeding
        ▼
   geo_node                ← geocode cities, route, drive-times, sunrise/sunset
        ▼
   synthesizer_node        ← Cerebras Qwen-3-235B + Groq fallback
        ▼
   supabase_writer         ← itinerary_days + stops + research_jobs
```

---

## Local setup

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
# install dependencies into a managed venv
uv sync

# copy env template and fill in keys
cp .env.example .env
# edit .env — see required vars below

# run the API
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify:

```bash
curl http://localhost:8000/agent/health
# → {"status":"ok","service":"nomad-agent"}
```

---

## Running the pipeline locally (no FastAPI round-trip)

```bash
uv run python scripts/run_agent_locally.py
# or with a specific sample:
uv run python scripts/run_pipeline.py samples/goa-december.json
```

Each sample in `samples/` prints signals + discoveries + final AIItinerary JSON to stdout. Output also lands in `out/<destination>.json`.

Individual agent runners for fast iteration:

```bash
uv run python scripts/run_youtube_agent_locally.py goa
uv run python scripts/run_youtube_longform_agent_locally.py rajasthan
uv run python scripts/run_google_blog_agent_locally.py goa
```

---

## Running tests

```bash
uv run pytest
# fast subset (no API keys, no network):
uv run pytest tests/test_schemas.py tests/test_signals.py tests/test_eval.py
```

187 tests pass on the current main branch. Eval harness (`test_eval.py`) requires score ≥ 85 after any synthesizer or signals changes.

---

## Project structure

```
nomad-agent/
├── pyproject.toml
├── .env.example
├── README.md           ← you are here
├── SYSTEM_DESIGN.md    ← architecture deep-dive (Redis, geo, model routing)
├── IMPROVEMENT_PLAN.md ← quality workstream with milestones A–D
├── SIGNOFF.md          ← completed refinement sign-off
├── BENCHMARK.md        ← itinerary quality benchmarks (Sprint 2–7)
│
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   ├── config.py                # pydantic-settings env loader
│   ├── auth.py                  # INTERNAL_AGENT_SECRET check
│   ├── schemas.py               # Pydantic models (mirror of Zod side)
│   ├── signals.py               # SignalExtractor — pure Python
│   ├── cache.py                 # Redis L1 destination + geocode cache
│   ├── llm/
│   │   └── factory.py           # get_llm("<role>") → provider per role
│   ├── agents/
│   │   ├── youtube_shorts.py    # <60s Shorts → discoveries (2-pass LLM)
│   │   ├── youtube_longform.py  # 4–25 min vlogs w/ mandatory transcripts
│   │   ├── reddit.py            # Reddit JSON → discoveries (destination-filtered)
│   │   ├── google_blog.py       # Tavily web search → discoveries (named-entity gated)
│   │   └── synthesizer.py       # all discoveries + geo brief → AIItinerary
│   ├── tools/                   # YouTube / Reddit / Tavily API wrappers
│   ├── geo/                     # OSM Nominatim + haversine + sunrise/sunset
│   ├── skills/                  # Prompt markdown files loaded at runtime
│   │   ├── synthesizer.md
│   │   ├── reddit_research.md
│   │   ├── blog_research.md
│   │   ├── blog_anchor.md
│   │   ├── youtube_pass1.md
│   │   ├── youtube_pass2.md
│   │   ├── regions/             # india.md, europe.md, southeast_asia.md
│   │   ├── trip_shapes/         # region_multi_city.md
│   │   └── vibes/               # food_and_markets.md
│   ├── graph/pipeline.py        # LangGraph state machine
│   ├── db/supabase_writer.py    # Supabase write functions (idempotent)
│   └── routes/research.py       # POST /agent/research, GET /agent/health
│
├── scripts/
│   ├── run_pipeline.py              # Full pipeline, no FastAPI
│   ├── run_agent_locally.py         # Same, loads default fixture
│   ├── run_youtube_agent_locally.py
│   ├── run_youtube_longform_agent_locally.py
│   └── run_google_blog_agent_locally.py
│
├── samples/                    # Test inputs (destination JSON files)
├── out/                        # Pipeline output artefacts (JSON + logs)
│
└── tests/
    ├── test_signals.py
    ├── test_schemas.py
    ├── test_eval.py            # Itinerary quality harness (score >= 85)
    ├── test_synthesizer.py
    ├── test_reddit_agent.py
    ├── test_youtube_longform_agent.py
    └── fixtures/sample_trip.json
```

---

## Required env vars

**Boot (always required):**
- `INTERNAL_AGENT_SECRET` — must match what Node sends; any long random string
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`

**Per-agent (only when that agent runs):**
- `YOUTUBE_API_KEY` — YouTube Data API v3 (free 10k units/day)
- `TAVILY_API_KEY` — for GoogleBlog agent (1k searches/month free)
- `GROQ_API_KEY` — default for all research agents (Llama-3.3-70B, ~100k tok/day)
- `CEREBRAS_API_KEY` — synthesizer primary model (Qwen-3-235B, 1M tok/day free)

**Optional:**
- `REDIS_URL` — activates L1 destination + geocode cache. Graceful no-op if absent.
- `OPENAI_API_KEY`, `GEMINI_API_KEY`, `TOGETHER_API_KEY` — opt-in alternate providers
- `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true` — LangSmith observability

---

## Quality benchmarks

See `BENCHMARK.md` for the full sprint-by-sprint quality history. Current state (post-Milestone D):

| Metric | Baseline (Sprint 7) | Current |
|--------|---------------------|---------|
| `source="maps"` stop share | 73% | ~25–35% |
| Route backtracking | Present | Eliminated (geo-routing) |
| Irrelevant/season-wrong warnings | Present | Fixed (season-gated signals) |
| Warm-path LLM calls (cached dest) | ~13–15 | 1–2 |
| Benchmark score vs reference | ~4/10 | ~8/10 |

See `out/rajasthan-tier1-benchmark.md` for the latest detailed run.
