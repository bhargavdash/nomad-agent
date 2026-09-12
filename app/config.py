"""Centralized configuration via pydantic-settings.

All env vars are loaded here. Required vars raise on missing; optional
vars (per-provider keys) only fail when their associated provider is
actually requested via the LLM factory.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Required infrastructure ---
    DATABASE_URL: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    INTERNAL_AGENT_SECRET: str = ""

    # --- Cache (Milestone C) ---
    # Redis connection. Empty → caching is DISABLED and the pipeline runs cold
    # (every request re-researches). Never a hard dependency.
    REDIS_URL: str = ""
    # Bump to invalidate all cached research at once (e.g. after a schema/agent
    # change). Part of every cache key.
    CACHE_VERSION: str = "v2"
    # Destination research is reusable but ages — bound staleness.
    RESEARCH_CACHE_TTL_DAYS: int = 45

    # --- Tool API keys ---
    YOUTUBE_API_KEY: str = ""  # required for YT agent
    TAVILY_API_KEY: str = ""  # required for Google blog agent

    # --- Place imagery (hero + per-city photos, resolved at build time) ---
    # Optional long-tail stock-photo search; empty → Wikipedia-only resolution.
    PEXELS_API_KEY: str = ""
    # Public Supabase Storage bucket the agent uploads self-hosted photos into.
    # Must exist + be public for self-hosting; if upload fails the build phase
    # falls back to writing the upstream URL (still works, just not self-hosted).
    SUPABASE_IMAGE_BUCKET: str = "place-images"

    # --- Per-role LLM provider/model selection ---
    LLM_YOUTUBE_PROVIDER: str = "groq"
    LLM_YOUTUBE_MODEL: str = "qwen/qwen3.8-27b"
    # Long-form YouTube agent: same defaults as Shorts; override per-env if a
    # bigger model is justified for the longer transcript context.
    LLM_YOUTUBE_LONGFORM_PROVIDER: str = "groq"
    LLM_YOUTUBE_LONGFORM_MODEL: str = "qwen/qwen3.8-27b"
    LLM_REDDIT_PROVIDER: str = "groq"
    LLM_REDDIT_MODEL: str = "qwen/qwen3.8-27b"
    LLM_GOOGLE_PROVIDER: str = "groq"
    LLM_GOOGLE_MODEL: str = "qwen/qwen3.8-27b"
    # Synthesizer: quality-critical. Cerebras gpt-oss-120b (structured JSON,
    # ~120B) with Groq openai/gpt-oss-120b fallback. qwen-3.8-27b is the
    # Cerebras default for smaller structured calls (geo / trending).
    LLM_SYNTH_PROVIDER: str = "cerebras"
    LLM_SYNTH_MODEL: str = "gpt-oss-120b"
    LLM_SYNTH_FALLBACK_PROVIDER: str = "groq"
    LLM_SYNTH_FALLBACK_MODEL: str = "openai/gpt-oss-120b"
    # Cerebras' free tier can queue at peak hours and block a request. Bound the
    # wait so a queue-block surfaces as a timeout *exception* — which the
    # synthesizer's get_structured_llm fallback routes to Groq — instead of
    # hanging the user's request. A normal synth call completes in well under
    # this budget; if it doesn't, we'd rather fall back than wait.
    LLM_CEREBRAS_TIMEOUT_SECONDS: float = 45.0
    LLM_CEREBRAS_MAX_RETRIES: int = 1
    # Tiny LLM call used only when the keyword-based region map misses; one
    # cached call per destination, ~50 tokens out. Cheap model is fine.
    LLM_SIGNALS_CLASSIFIER_PROVIDER: str = "groq"
    LLM_SIGNALS_CLASSIFIER_MODEL: str = "qwen/qwen3.8-27b"
    # Geo city-circuit picker (Milestone D) — small output (ordered city list).
    LLM_GEO_PLANNER_PROVIDER: str = "cerebras"
    LLM_GEO_PLANNER_MODEL: str = "qwen-3.8-27b"
    # SA-8 trending agent — single seasonal call producing 10 India + 10
    # international destinations with blurbs.
    LLM_TRENDING_PROVIDER: str = "cerebras"
    LLM_TRENDING_MODEL: str = "qwen-3.8-27b"

    # --- Provider API keys (optional — only checked when used) ---
    GROQ_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    TOGETHER_API_KEY: str = ""
    CEREBRAS_API_KEY: str = ""

    # Optional: observability. Set LANGSMITH_TRACING=true + LANGSMITH_API_KEY to
    # get full per-call traces (incl. token counts + latency) in LangSmith.
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_TRACING: str = ""
    LANGSMITH_PROJECT: str = "nomad-agent"


settings = Settings()
