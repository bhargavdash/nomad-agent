"""Observability wiring (Milestone follow-up).

LangChain/LangSmith reads its tracing config from `os.environ`, but
pydantic-settings loads `.env` into the `settings` object — *not* into
`os.environ`. This bridges the two so that simply setting `LANGSMITH_TRACING`
+ `LANGSMITH_API_KEY` in `.env` turns on full traces (per-call token counts +
latency) without any code change.

`configure_observability()` is called explicitly at process startup (FastAPI
app + the run scripts) — NOT at import — so the test suite never enables
tracing or makes network calls to LangSmith.
"""

from __future__ import annotations

import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def configure_observability() -> None:
    """Enable LangSmith tracing if configured; otherwise a no-op (logged)."""
    tracing_on = settings.LANGSMITH_TRACING.strip().lower() in _TRUTHY
    if tracing_on and settings.LANGSMITH_API_KEY:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", settings.LANGSMITH_API_KEY)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT or "nomad-agent")
        logger.info(
            "observability: LangSmith tracing ENABLED (project=%s)",
            os.environ["LANGSMITH_PROJECT"],
        )
    else:
        logger.info(
            "observability: LangSmith tracing OFF "
            "(set LANGSMITH_TRACING=true + LANGSMITH_API_KEY to enable)"
        )
