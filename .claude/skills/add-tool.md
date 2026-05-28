---
name: add-tool
description: Scaffold a new external API tool for nomad-agent (e.g. Instagram, TikTok, Substack) following the YouTube/Reddit/Tavily pattern
---

When adding a new data-source tool to nomad-agent, follow this pattern. Tools are thin API wrappers — no LLM logic, no state mutation.

## File location

`app/tools/<source_name>.py` — e.g. `app/tools/instagram.py`

## Tool template

```python
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def fetch_<source>_results(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Fetch results from <Source> for the given query."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "<API_ENDPOINT>",
                params={"q": query, "limit": max_results},
                headers={"Authorization": f"Bearer {settings.<API_KEY>}"},
            )
            response.raise_for_status()
            return response.json().get("items", [])
    except Exception:
        logger.exception("Failed to fetch from <Source> for query=%r", query)
        return []
```

## Rules

1. **Always return `[]` on failure** — tools must never raise. Log the exception, return empty.
2. **Use `httpx.AsyncClient`** — never `requests` (sync) in an async context
3. **Timeout every request** — `timeout=10.0` minimum; external APIs stall
4. **No LLM calls in tools** — tools are pure data fetching. LLM processing goes in the agent that uses the tool.
5. **Read API keys from `app/config.py`** — never hardcode or `os.environ.get` directly in the tool
6. **Type the return value** — `list[dict[str, Any]]` is the minimum; prefer a typed dataclass if the shape is stable

## Wiring to a new agent

After creating the tool, create or update an agent in `app/agents/<source_name>.py`:

```python
from app.tools.<source_name> import fetch_<source>_results
from app.schemas import ResearchDiscovery

async def run(query: str, signals: dict) -> list[ResearchDiscovery]:
    try:
        raw = await fetch_<source>_results(query)
        # transform raw → ResearchDiscovery with LLM or heuristic
        return discoveries
    except Exception:
        logger.exception("<Source> agent failed")
        return []
```

Then add the agent as a parallel node in `app/graph/pipeline.py` following the existing YouTube/Reddit/Google pattern. Update `add-research-agent.md` skill if this is a full new research agent.

## Config addition

Add the new API key to `app/config.py`:
```python
class Settings(BaseSettings):
    # ... existing ...
    instagram_api_key: str = ""  # optional — agent degrades gracefully if empty
```

And to `.env.example`.
