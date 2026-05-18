"""LLM factory — single point of model selection per agent role.

Every agent obtains its LLM via `get_llm("<role>")`. Provider and model
are read from config (env vars) so swapping models is a one-line change.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from app.config import settings


def _resolve_role(role: str) -> tuple[str, str]:
    """Return (provider, model) for the given role."""
    mapping = {
        "youtube_agent": (settings.LLM_YOUTUBE_PROVIDER, settings.LLM_YOUTUBE_MODEL),
        "youtube_longform_agent": (
            settings.LLM_YOUTUBE_LONGFORM_PROVIDER,
            settings.LLM_YOUTUBE_LONGFORM_MODEL,
        ),
        "reddit_agent": (settings.LLM_REDDIT_PROVIDER, settings.LLM_REDDIT_MODEL),
        "google_agent": (settings.LLM_GOOGLE_PROVIDER, settings.LLM_GOOGLE_MODEL),
        "synthesizer": (settings.LLM_SYNTH_PROVIDER, settings.LLM_SYNTH_MODEL),
        "signals_classifier": (
            settings.LLM_SIGNALS_CLASSIFIER_PROVIDER,
            settings.LLM_SIGNALS_CLASSIFIER_MODEL,
        ),
    }
    if role not in mapping:
        raise ValueError(
            f"Unknown LLM role: {role!r}. Expected one of {list(mapping)}."
        )
    return mapping[role]


def get_llm(role: str) -> BaseChatModel:
    """Return a configured LangChain chat model for the given agent role.

    role: one of "youtube_agent" | "youtube_longform_agent" | "reddit_agent"
          | "google_agent" | "synthesizer" | "signals_classifier".
    """
    provider, model = _resolve_role(role)
    provider = provider.lower()

    if provider == "groq":
        from langchain_groq import ChatGroq

        if not settings.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set but provider 'groq' was requested.")
        return ChatGroq(model=model, api_key=settings.GROQ_API_KEY)

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set but provider 'openai' was requested.")
        return ChatOpenAI(model=model, api_key=settings.OPENAI_API_KEY)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set but provider 'anthropic' was requested."
            )
        return ChatAnthropic(model=model, api_key=settings.ANTHROPIC_API_KEY)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set but provider 'google' was requested.")
        return ChatGoogleGenerativeAI(model=model, google_api_key=settings.GEMINI_API_KEY)

    if provider == "together":
        from langchain_openai import ChatOpenAI

        if not settings.TOGETHER_API_KEY:
            raise RuntimeError(
                "TOGETHER_API_KEY is not set but provider 'together' was requested."
            )
        return ChatOpenAI(
            model=model,
            api_key=settings.TOGETHER_API_KEY,
            base_url="https://api.together.xyz/v1",
        )

    if provider == "kimi":
        from langchain_openai import ChatOpenAI

        # Kimi/Moonshot uses an OpenAI-compatible API. Reuse OPENAI_API_KEY slot
        # or set MOONSHOT_API_KEY via OPENAI_API_KEY for now.
        if not settings.OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY (used as Moonshot key) is not set but provider 'kimi' was requested."
            )
        return ChatOpenAI(
            model=model,
            api_key=settings.OPENAI_API_KEY,
            base_url="https://api.moonshot.cn/v1",
        )

    if provider == "cerebras":
        from langchain_openai import ChatOpenAI

        if not settings.CEREBRAS_API_KEY:
            raise RuntimeError(
                "CEREBRAS_API_KEY is not set but provider 'cerebras' was requested."
            )
        return ChatOpenAI(
            model=model,
            api_key=settings.CEREBRAS_API_KEY,
            base_url="https://api.cerebras.ai/v1",
        )

    raise ValueError(
        f"Unknown LLM provider: {provider!r}. "
        "Expected one of: groq, openai, anthropic, google, together, kimi, cerebras."
    )
