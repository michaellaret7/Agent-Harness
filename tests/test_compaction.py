"""Context compaction contracts. Run: uv run --all-packages pytest tests/test_compaction.py"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from openai import OpenAI

from agent_harness.client import resolve_context_window


#     ================================
# --> Helper funcs
#     ================================


def models_client(entries: list[dict[str, Any]]) -> OpenAI:
    """An OpenAI client whose `/models` endpoint returns `entries`, served in memory."""
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith('/models')

        return httpx.Response(200, json={'object': 'list', 'data': entries})

    http_client = httpx.Client(transport=httpx.MockTransport(respond))

    return OpenAI(api_key='test-only', base_url='https://harness.invalid/v1',
                  http_client=http_client, max_retries=0)


#     ================================
# --> Context window
#     ================================


def test_context_window_reads_each_providers_field() -> None:
    """OpenRouter's `context_length` and vLLM's `max_model_len` both resolve to the window."""
    openrouter = models_client([
        {'id': 'other/model', 'object': 'model', 'created': 0, 'owned_by': 'x', 'context_length': 8_000},
        {'id': 'anthropic/claude', 'object': 'model', 'created': 0, 'owned_by': 'x', 'context_length': 200_000},
    ])
    vllm = models_client([
        {'id': 'qwen', 'object': 'model', 'created': 0, 'owned_by': 'vllm', 'max_model_len': 32_768},
    ])

    assert resolve_context_window(openrouter, 'openrouter', 'anthropic/claude') == 200_000
    assert resolve_context_window(vllm, 'vllm', 'qwen') == 32_768


def test_context_window_unknown_model_raises() -> None:
    """A model the provider does not list, or lists without a window, fails fast."""
    client = models_client([
        {'id': 'listed', 'object': 'model', 'created': 0, 'owned_by': 'x'},
    ])

    with pytest.raises(RuntimeError, match='context_window='):
        resolve_context_window(client, 'openrouter', 'missing')

    with pytest.raises(RuntimeError, match='context_window='):
        resolve_context_window(client, 'openrouter', 'listed')
