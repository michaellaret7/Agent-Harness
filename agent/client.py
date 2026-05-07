"""Build an OpenAI-compatible client for Anthropic, OpenAI, or our hosted vLLM (RunPod)."""
from __future__ import annotations

import os

from openai import OpenAI

VLLM_PLACEHOLDER_KEY = 'placeholder'  # hosted vLLM endpoint does not require auth

# provider -> (api_key env var, base_url env var)
HOSTED_PROVIDER_ENV: dict[str, tuple[str, str]] = {
    'anthropic': ('ANTHROPIC_API_KEY', 'ANTHROPIC_API_URL'),
    'openai': ('OPENAI_API_KEY', 'OPENAI_API_URL'),
}

def build_client(provider: str = 'vllm', model: str | None = None) -> tuple[OpenAI, str]:
    """Return (client, model). provider is 'anthropic', 'openai', or 'vllm'."""

    provider = provider.lower()

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

    key_var, url_var = HOSTED_PROVIDER_ENV[provider]
    api_key = os.getenv(key_var)

    if not api_key:
        raise RuntimeError(f'missing env var {key_var} for provider {provider!r}')

    return OpenAI(api_key=api_key, base_url=os.getenv(url_var)), model
