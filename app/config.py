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

    # --- Tool API keys ---
    YOUTUBE_API_KEY: str = ""  # required for YT agent
    TAVILY_API_KEY: str = ""  # required for Google blog agent

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
    LLM_SYNTH_PROVIDER: str = "anthropic"
    LLM_SYNTH_MODEL: str = "claude-sonnet-4-6"

    # --- Provider API keys (optional — only checked when used) ---
    GROQ_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    TOGETHER_API_KEY: str = ""
    CEREBRAS_API_KEY: str = ""

    # Optional: observability
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_TRACING: str = ""


settings = Settings()
