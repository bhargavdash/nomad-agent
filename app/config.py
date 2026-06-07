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
    LLM_YOUTUBE_MODEL: str = "llama-3.3-70b-versatile"
    # Long-form YouTube agent: same defaults as Shorts; override per-env if a
    # bigger model is justified for the longer transcript context.
    LLM_YOUTUBE_LONGFORM_PROVIDER: str = "groq"
    LLM_YOUTUBE_LONGFORM_MODEL: str = "llama-3.3-70b-versatile"
    LLM_REDDIT_PROVIDER: str = "groq"
    LLM_REDDIT_MODEL: str = "llama-3.3-70b-versatile"
    LLM_GOOGLE_PROVIDER: str = "groq"
    LLM_GOOGLE_MODEL: str = "llama-3.3-70b-versatile"
    # Synthesizer: quality-critical step. Default to Cerebras' GLM-4.7 model
    # (free tier, far stronger than 70B), with a Groq 70B fallback so a
    # free-tier queue/error never turns into a hard failure. If the
    # fallback provider/model equals the primary, no fallback is attached.
    LLM_SYNTH_PROVIDER: str = "cerebras"
    LLM_SYNTH_MODEL: str = "zai-glm-4.7"
    LLM_SYNTH_FALLBACK_PROVIDER: str = "groq"
    LLM_SYNTH_FALLBACK_MODEL: str = "llama-3.3-70b-versatile"
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
    LLM_SIGNALS_CLASSIFIER_MODEL: str = "llama-3.3-70b-versatile"
    # Geo city-circuit picker (Milestone D) — small output (ordered city list).
    # Defaults to Cerebras: it's a tiny call, and keeping it off Groq avoids
    # competing with the research agents for Groq's tight ~100k tokens/day cap
    # (which would silently degrade the geo layer).
    LLM_GEO_PLANNER_PROVIDER: str = "cerebras"
    LLM_GEO_PLANNER_MODEL: str = "zai-glm-4.7"
    # SA-8 trending agent — single seasonal call producing 10 India + 10
    # international destinations with blurbs. Defaults to Cerebras GLM-4.7
    # because the call is small, the output is structured JSON, and the
    # cadence is ~4 calls/year so cost is effectively zero.
    LLM_TRENDING_PROVIDER: str = "cerebras"
    LLM_TRENDING_MODEL: str = "zai-glm-4.7"

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
