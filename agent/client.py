"""Build an OpenAI-compatible client for a given model provider."""
from __future__ import annotations

import os

from openai import OpenAI

LOCAL_API_KEY = 'placeholder'
LOCAL_BASE_URL = 'http://localhost:8000/v1'

# provider -> (api_key env var, base_url env var)
PROVIDER_ENV: dict[str, tuple[str, str]] = {
    'anthropic': ('ANTHROPIC_API_KEY', 'ANTHROPIC_API_URL'),
    'openai': ('OPENAI_API_KEY', 'OPENAI_API_URL'),
}


def build_client(
    model_provider: str,
    model_name: str,
    local: bool = False,
) -> tuple[OpenAI, str]:
    """Return (client, model_name). Pass local=True to hit the dev server."""

    if local:
        model_name = 'nemotron3-nano-4b-fp8'
        return OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_BASE_URL), model_name

    provider = model_provider.lower()
    
    if provider not in PROVIDER_ENV:
        raise ValueError(
            f'unknown model_provider {model_provider!r}; '
            f'expected one of {sorted(PROVIDER_ENV)} (or pass local=True)'
        )

    key_var, url_var = PROVIDER_ENV[provider]
    api_key = os.getenv(key_var)

    if not api_key:
        raise RuntimeError(
            f'missing env var {key_var} for provider {provider!r}; '
            f'set it in .env or pass local=True for the dev server'
        )

    return OpenAI(api_key=api_key, base_url=os.getenv(url_var)), model_name
