"""Build an OpenAI-compatible client for OpenRouter or our hosted vLLM (RunPod).

When LANGFUSE_PUBLIC_KEY is set, importing `langfuse.openai` monkey-patches
`openai.resources.chat.completions.Completions.create` (via wrapt) so every
call is auto-captured as a Langfuse generation under whatever span is
active at call time. The patch is process-global — the agent loop never
sees it.
"""
from __future__ import annotations

import os

from openai import OpenAI

VLLM_PLACEHOLDER_KEY = 'placeholder'  # hosted vLLM endpoint does not require auth

def _maybe_enable_langfuse_instrumentation() -> None:
    """Trigger the langfuse.openai import side effect when keys are present.

    Reads the already-populated environment — the application entry point
    is responsible for `load_dotenv()` before constructing an Agent.
    Idempotent: re-importing the module is a no-op.
    """
    if os.getenv('LANGFUSE_PUBLIC_KEY'):
        import langfuse.openai  # noqa: F401  (import-for-side-effect)

# provider -> (api_key env var, base_url env var, default base_url)
# The default matters: with base_url=None the OpenAI SDK falls back to api.openai.com
HOSTED_PROVIDER_ENV: dict[str, tuple[str, str, str]] = {
    'openrouter': ('OPENROUTER_API_KEY', 'OPENROUTER_API_URL', 'https://openrouter.ai/api/v1'),
}

# provider -> the `/models` field holding the model's maximum context length
CONTEXT_WINDOW_FIELD: dict[str, str] = {
    'openrouter': 'context_length',
    'vllm': 'max_model_len',
}

def build_client(provider: str = 'openrouter', model: str | None = None) -> tuple[OpenAI, str]:
    """Return (client, model). provider defaults to 'openrouter'; 'vllm' is opt-in.

    Reads credentials from the process environment. The application entry
    point must have loaded `.env` (via `load_dotenv()`) before calling this
    — `agent_harness/` reads config, it does not load it.
    """
    provider = provider.lower()
    
    _maybe_enable_langfuse_instrumentation()

    if provider == 'vllm':
        base_url = os.getenv('VLLM_API_URL')
        model = model or os.getenv('VLLM_MODEL')

        if not base_url:
            raise RuntimeError('missing env var VLLM_API_URL')

        if not model:
            raise RuntimeError('missing model: set VLLM_MODEL or pass model=')

        return OpenAI(api_key=VLLM_PLACEHOLDER_KEY, base_url=base_url), model

    if provider not in HOSTED_PROVIDER_ENV:
        raise ValueError(
            f"unknown provider {provider!r}; "
            f"expected 'vllm' or one of {sorted(HOSTED_PROVIDER_ENV)}"
        )

    if not model:
        raise ValueError(f"model is required for provider {provider!r}")

    key_var, url_var, default_url = HOSTED_PROVIDER_ENV[provider]
    api_key = os.getenv(key_var)

    if not api_key:
        raise RuntimeError(f'missing env var {key_var} for provider {provider!r}')

    return OpenAI(api_key=api_key, base_url=os.getenv(url_var) or default_url), model


def resolve_context_window(client: OpenAI, provider: str, model: str) -> int:
    """Return the model's maximum context length in tokens, as the provider reports it.

    OpenRouter lists it as `context_length` on `/models`; vLLM as `max_model_len`
    on `/v1/models`. Both are non-standard fields the SDK keeps as extras.
    Raises when the provider does not list the model, so callers can fall back
    to an explicit `CompactionConfig(context_window=...)`.
    """
    field = CONTEXT_WINDOW_FIELD[provider.lower()]

    for entry in client.models.list():
        value = getattr(entry, field, None)

        if entry.id == model and value:
            return int(value)

    raise RuntimeError(
        f'context window unknown for model {model!r} on provider {provider!r}; '
        'pass CompactionConfig(context_window=...)'
    )
