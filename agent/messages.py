"""Constructors for chat-completion message dicts.

These return plain dicts in the exact shape the OpenAI SDK expects on the
wire — no wrapper types, no flattening step. The only purpose is to make
the call sites self-documenting and to give every message shape exactly
one source of truth.
"""
from __future__ import annotations

from typing import Any


def system_msg(content: str) -> dict[str, Any]:
    return {'role': 'system', 'content': content}


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
