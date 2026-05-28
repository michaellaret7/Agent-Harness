"""Formatting helpers shared by Sink implementations.

`format_args_inline` and `format_tool_summary` produce the compact
one-line strings used by both StdoutSink and LogSink. Kept here so the
two sinks can't drift out of sync on how a tool call is rendered.
"""
from __future__ import annotations

import json
from typing import Any

from agent.sinks.protocol import ToolOutcome


def _format_arg_value(value: Any, max_len: int = 80) -> str:
    """Render one tool-arg value as a compact single-line string."""
    if isinstance(value, str):
        shown = value[:max_len] + '...' if len(value) > max_len else value
        return f'"{shown}"'

    if isinstance(value, list):
        rendered = json.dumps(value, ensure_ascii=False)

        if len(rendered) > max_len:
            return f'[{len(value)} items]'

        return rendered

    if isinstance(value, dict):
        rendered = json.dumps(value, ensure_ascii=False)

        if len(rendered) > max_len:
            return f'{{{len(value)} keys}}'

        return rendered

    return json.dumps(value, ensure_ascii=False)


def format_args_inline(args_json: str) -> str:
    """Render tool args as a Python-style function-call argument string.

    Empty args → empty string (renders as `Name()`). Malformed JSON falls
    back to a truncated raw snippet so the model's mistake is still visible.
    """
    if not args_json or args_json.strip() in ('', '{}'):
        return ''

    try:
        parsed = json.loads(args_json)

    except (json.JSONDecodeError, ValueError):
        return args_json[:160] + ('...' if len(args_json) > 160 else '')

    if not isinstance(parsed, dict) or not parsed:
        return ''

    return ', '.join(f'{k}={_format_arg_value(v)}' for k, v in parsed.items())


def format_tool_summary(outcome: ToolOutcome) -> str:
    """One-line tool-end summary.

    Asymmetric by design: successful runs show only duration + size (the
    content is the LLM's problem, not the operator's). Errors and
    interrupts preserve the message so failures are debuggable at a glance.

    Tokens are estimated as `chars / 4` — the standard rough heuristic for
    OpenAI/Anthropic BPE tokenizers on English text.
    """
    if outcome.status != 'ok':
        message = (outcome.payload or '').strip()

        if len(message) > 200:
            message = message[:200] + '...'

        return f'{outcome.duration:.1f}s · {message}'

    tokens = max(1, len(outcome.payload) // 4)

    return f'{outcome.duration:.1f}s · ~{tokens} tokens'
