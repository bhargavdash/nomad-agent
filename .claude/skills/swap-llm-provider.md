# Skill: Swap an LLM Provider / Model

The whole point of this service's `app/llm/factory.py` is that swapping the model under any role is a one-line config change. This skill covers both swap-an-existing-provider and add-a-new-provider.

## Case 1 — Swap one role to a different existing provider

Example: move the synthesizer from Anthropic Claude to Groq Llama.

1. Set env vars:
   ```env
   LLM_SYNTH_PROVIDER=groq
   LLM_SYNTH_MODEL=llama-3.3-70b-versatile
   GROQ_API_KEY=...
   ```
2. Restart the FastAPI server. No code change.

The LLM factory ([app/llm/factory.py](../../app/llm/factory.py)) reads `LLM_<ROLE>_PROVIDER` / `LLM_<ROLE>_MODEL` from `app/config.py` and dispatches to the right `langchain-*` package.

Available roles:
- `youtube_agent` — used by `app/agents/youtube_shorts.py`
- `reddit_agent`
- `google_agent`
- `synthesizer`

Available providers (already wired):
- `groq` — needs `GROQ_API_KEY`
- `openai` — needs `OPENAI_API_KEY`
- `anthropic` — needs `ANTHROPIC_API_KEY`
- `google` — needs `GEMINI_API_KEY`
- `together` — needs `TOGETHER_API_KEY` (uses `ChatOpenAI` w/ `base_url=https://api.together.xyz/v1`)
- `kimi` — uses `OPENAI_API_KEY` slot, `base_url=https://api.moonshot.cn/v1`

## Case 2 — Add a new provider to the factory

Example: add Mistral via `langchain-mistralai`.

1. Add the dep:
   ```bash
   uv add langchain-mistralai
   ```
2. Add the API key field to `app/config.py`:
   ```python
   MISTRAL_API_KEY: str = ""
   ```
   And to `.env.example`.
3. Add a branch in `app/llm/factory.py`'s `get_llm` following the existing pattern:
   ```python
   if provider == "mistral":
       from langchain_mistralai import ChatMistralAI

       if not settings.MISTRAL_API_KEY:
           raise RuntimeError("MISTRAL_API_KEY is not set but provider 'mistral' was requested.")
       return ChatMistralAI(model=model, api_key=settings.MISTRAL_API_KEY)
   ```
4. Update the final `raise ValueError(...)` to mention `mistral` in the supported list.
5. Switch a role to use it via env, no further code changes.

## Case 3 — Per-provider tuning

If a provider needs custom params (temperature, JSON mode, max tokens), set them **inside the factory branch**, not in agent code. Agent code stays provider-agnostic — that's the whole architectural promise. If you find yourself doing `if isinstance(llm, ChatAnthropic)` in an agent, you're undoing the design.

## Verification

After any swap:
```bash
uv run python scripts/run_youtube_agent_locally.py    # exercises one role end-to-end
uv run python scripts/run_agent_locally.py            # exercises the full pipeline
```

If LangSmith is enabled (`LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`), the trace will show the new model name on the LLM call — easy way to confirm the swap took effect.

## Cost guardrails

- Defaults in `app/config.py` are tuned for **free / cheap**: Groq Llama 3.3 for the 3 research agents, Anthropic Claude Sonnet 4.6 for the synthesizer (paid but only one call per trip).
- If switching a research agent to a paid model, watch the per-trip cost — there are 3 of them and they fan out per query.
- The synthesizer prompt is the longest; it's where flagship-model dollars are best spent.

## Don't

- Don't put `import langchain_mistralai` at the top of `factory.py` — keep imports inside each branch so missing optional packages don't break startup.
- Don't add a provider without an API-key check that raises a clear `RuntimeError`.
- Don't read env vars directly in agent code — go through `app/config.py`.
