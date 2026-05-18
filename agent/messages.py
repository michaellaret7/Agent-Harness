"""Constructors for chat-completion message dicts.

These return plain dicts in the exact shape the OpenAI SDK expects on the
wire — no wrapper types, no flattening step. The only purpose is to make
the call sites self-documenting and to give every message shape exactly
one source of truth.
"""
from __future__ import annotations

from typing import Any


def cached_text(text: str) -> list[dict[str, Any]]:
    """Wrap text as a content-parts list with an Anthropic-style cache breakpoint.

    OpenRouter reads `cache_control` and forwards to upstream caching
    (Anthropic explicit, Gemini explicit, etc.). Models without caching
    support silently drop the field.
    """
    return [{'type': 'text', 'text': text, 'cache_control': {'type': 'ephemeral'}}]


def system_msg(content: str, cache: bool = False) -> dict[str, Any]:
    return {'role': 'system', 'content': cached_text(content) if cache else content}


def user_msg(content: str) -> dict[str, Any]:
    return {'role': 'user', 'content': content}


def assistant_msg(content: str, tool_calls: list[dict] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {'role': 'assistant', 'content': content}

    if tool_calls:
        msg['tool_calls'] = tool_calls

    return msg


def tool_msg(tool_call_id: str, content: str) -> dict[str, Any]:
    return {
        'role': 'tool',
        'tool_call_id': tool_call_id,
        'content': content,
    }
