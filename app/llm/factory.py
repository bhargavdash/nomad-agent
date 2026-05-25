"""LLM factory — single point of model selection per agent role.

Every agent obtains its LLM via `get_llm("<role>")`. Provider and model
are read from config (env vars) so swapping models is a one-line change.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from app.config import settings

logger = logging.getLogger(__name__)


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


def _resolve_fallback(role: str) -> tuple[str, str] | None:
    """Return (provider, model) for a role's fallback, or None if none configured.

    Only the synthesizer has a fallback today (Cerebras-235B primary → Groq-70B
    fallback) so a free-tier queue/error never becomes a hard failure. Returns
    None when no fallback is set or when it would duplicate the primary.
    """
    if role != "synthesizer":
        return None
    provider = settings.LLM_SYNTH_FALLBACK_PROVIDER
    model = settings.LLM_SYNTH_FALLBACK_MODEL
    if not provider or not model:
        return None
    if (provider, model) == _resolve_role(role):
        return None  # fallback identical to primary — pointless
    return provider, model


def get_llm(role: str) -> BaseChatModel:
    """Return a configured LangChain chat model for the given agent role.

    role: one of "youtube_agent" | "youtube_longform_agent" | "reddit_agent"
          | "google_agent" | "synthesizer" | "signals_classifier".
    """
    provider, model = _resolve_role(role)
    return _build_llm(provider, model, role=role)


def _build_llm(provider: str, model: str, *, role: str = "?") -> BaseChatModel:
    """Instantiate a LangChain chat model for an explicit (provider, model)."""
    provider = provider.lower()
    logger.info("[LLM] role=%-28s  provider=%-10s  model=%s", role, provider, model)

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
        # timeout + bounded retries so a peak-time queue-block fails fast to the
        # Groq fallback (see get_structured_llm) rather than hanging the request.
        return ChatOpenAI(
            model=model,
            api_key=settings.CEREBRAS_API_KEY,
            base_url="https://api.cerebras.ai/v1",
            timeout=settings.LLM_CEREBRAS_TIMEOUT_SECONDS,
            max_retries=settings.LLM_CEREBRAS_MAX_RETRIES,
        )

    raise ValueError(
        f"Unknown LLM provider: {provider!r}. "
        "Expected one of: groq, openai, anthropic, google, together, kimi, cerebras."
    )


def _apply_structured(llm: BaseChatModel, schema: Any, method: str | None) -> Runnable:
    """Apply structured output, trying `method` first then the provider default.

    Providers vary in support for the `method` kwarg (e.g. json_mode); when it
    raises at construction, fall back to the default so we never hard-fail on a
    kwarg mismatch.
    """
    if method is not None:
        try:
            return llm.with_structured_output(schema, method=method)
        except Exception:  # noqa: BLE001
            pass
    return llm.with_structured_output(schema)


def get_structured_llm(
    role: str, schema: Any, *, method: str | None = None
) -> Runnable:
    """Return a structured-output runnable for `role`, with provider fallback.

    Equivalent to ``get_llm(role).with_structured_output(schema, method=...)``
    but, when the role defines a fallback (see ``_resolve_fallback``), the
    returned runnable transparently retries on the fallback provider if the
    primary errors at invocation time. This is what lets the synthesizer use
    Cerebras-235B as primary without losing reliability when the free tier
    queues — Groq-70B picks up. Callers still handle a final failure (the
    synthesizer degrades to its deterministic skeleton).
    """
    primary = _apply_structured(get_llm(role), schema, method)
    fallback = _resolve_fallback(role)
    if fallback is None:
        return primary
    fb_provider, fb_model = fallback
    try:
        fb_llm = _build_llm(fb_provider, fb_model, role=f"{role}:fallback")
    except Exception as e:  # noqa: BLE001
        # Fallback misconfigured (e.g. missing key) — run primary-only.
        logger.warning(
            "get_structured_llm: fallback unavailable for role=%s: %s", role, e
        )
        return primary
    return primary.with_fallbacks([_apply_structured(fb_llm, schema, method)])
