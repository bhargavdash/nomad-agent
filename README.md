# nomad-agent

The agentic AI service for the **Nomad** travel itinerary app. Python +
FastAPI + LangGraph. Companion to the Node `nomad-api` service: Node owns
auth, CRUD and polling; this service owns the multi-agent research +
synthesis pipeline. Both write to the same Supabase database.

The full architecture, agent strategies, prompts, and contract details
live in [`AI_INTEGRATION_PLAN.md`](../nomad-api/AI_INTEGRATION_PLAN.md)
in the `nomad-api` repo. **Read it first.**

---

## What this is

- **FastAPI** service exposing `POST /agent/research` (called by Node) and
  `GET /agent/health`.
- **LangGraph** pipeline running 3 research agents in parallel
  (`YouTubeShortsAgent`, `RedditAgent`, `GoogleBlogAgent`) followed by a
  `SynthesizerAgent` that produces the final day-by-day itinerary.
- **Signal extraction** layer (`app/signals.py`) — pure-Python derivation
  of season / festival / crowd / budget / vibe weights from trip params.
  Runs before any LLM and shapes every search query.
- **Model-agnostic.** Each agent's provider/model is set per-role via env
  vars (`LLM_YOUTUBE_PROVIDER`, `LLM_SYNTH_PROVIDER`, etc.). Swap Groq
  Llama for Anthropic Claude in one line.

This commit ships the **scaffold** only. The four agents are stubs that
return empty lists or a hardcoded placeholder itinerary. Real LLM calls
land in the next session (Sprint 2 in the plan).

---

## Local setup

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
# install dependencies into a managed venv
uv sync

# copy env template and fill in keys
cp .env.example .env
# edit .env — set DATABASE_URL, SUPABASE_*, INTERNAL_AGENT_SECRET, plus any
# provider keys for the LLMs you want to use (GROQ_API_KEY recommended).

# run the API
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify:

```bash
curl http://localhost:8000/agent/health
# → {"status":"ok","service":"nomad-agent"}
```

---

## Running the local script

For fast iteration without the FastAPI / Node round-trip:

```bash
uv run python scripts/run_agent_locally.py
```

This loads `tests/fixtures/sample_trip.json`, runs the LangGraph pipeline
end-to-end, and prints signals + discoveries + itinerary to stdout.

---

## Running tests

```bash
uv run pytest
# or just the implemented bits:
uv run pytest tests/test_schemas.py tests/test_signals.py
```

`signals.py` and `schemas.py` are fully tested. The agent stubs are not.

---

## Project structure

```
nomad-agent/
├── pyproject.toml
├── .env.example
├── README.md
│
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   ├── config.py                # pydantic-settings env loader
│   ├── auth.py                  # internal-secret check
│   ├── schemas.py               # Pydantic models (mirror of Zod side)
│   ├── signals.py               # SignalExtractor — pure Python
│   ├── llm/factory.py           # get_llm("<role>") → provider per role
│   ├── agents/                  # YouTubeShorts / Reddit / GoogleBlog / Synthesizer
│   ├── tools/                   # YouTube / Reddit / Tavily wrappers (tbd)
│   ├── graph/pipeline.py        # LangGraph state machine
│   ├── db/supabase_writer.py    # Supabase write functions
│   └── routes/research.py       # POST /agent/research
│
├── scripts/
│   └── run_agent_locally.py     # CLI entry, bypasses FastAPI
│
└── tests/
    ├── test_signals.py
    ├── test_schemas.py
    └── fixtures/sample_trip.json
```

See [`AI_INTEGRATION_PLAN.md`](../nomad-api/AI_INTEGRATION_PLAN.md) for
the why behind every choice.


Env vars to set before the next session
Required to boot:

INTERNAL_AGENT_SECRET — any long random string; must match what Node sends
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
DATABASE_URL (declared but only used if you wire raw SQL later)
Required when you implement the YouTube agent (Sprint 2 — next session):

YOUTUBE_API_KEY — YouTube Data API v3 key (free 10k units/day)
GROQ_API_KEY — for the default Llama-3.3 LLM the YT agent uses
Required for Sprint 3 agents:

TAVILY_API_KEY — for the GoogleBlogAgent
ANTHROPIC_API_KEY — for the default synthesizer model (Claude Sonnet 4.6)
Optional / opt-in providers (only if you switch a role's LLM_*_PROVIDER):

OPENAI_API_KEY, GEMINI_API_KEY, TOGETHER_API_KEY
Optional observability:

LANGSMITH_API_KEY + LANGSMITH_TRACING=true — strongly recommended once real LLM calls land
Notes / minor deviations
uv resolved Python 3.14 (your local default) instead of 3.12; requires-python = ">=3.12" so this is compatible. Pin to 3.12 with uv python pin 3.12 if you prefer.
One small fix vs. the prompt: the test "rejects invalid time format" was reworked to construct AIStop directly (Pydantic's model_copy(update=) bypasses validators) — same intent, valid coverage.
app/tools/ is an empty package for now; per the plan, Tools (youtube.py, reddit.py, tavily.py) get implemented alongside their respective agents in Sprint 2/3.
The kimi provider in the factory uses OPENAI_API_KEY as the credential slot since Moonshot is OpenAI-compatible — swap to a dedicated MOONSHOT_API_KEY if/when you start using it.
