"""Sink Protocol — the agent's output contract.

The agent loop calls these methods from a worker thread. Implementations
live wherever they make sense:
- `StdoutSink` (here) is the headless fallback for tests/scripts that
  import `Agent` without a TUI.
- `MultiSink` (here) fans events out to several downstream sinks.
- `TUISink` lives in `tui/sink.py` and mutates the prompt_toolkit History.
- `LangfuseSink` lives in `agent/sinks/langfuse.py` and mirrors events to
  Langfuse spans.

`on_turn_start` / `on_turn_end` bracket one call to `Agent.run`. They are
emitted by Agent.run itself, not the execution loop, so adding new
observability sinks does not require touching agent/loop.py.
"""
from __future__ import annotations

import difflib
import logging
from typing import Protocol

from agent.usage import Usage

log = logging.getLogger(__name__)


class Sink(Protocol):
    def on_turn_start(self, prompt: str) -> None: ...
    def on_turn_end(self, result: str) -> None: ...
    def on_user_message(self, text: str) -> None: ...
    def on_reasoning_delta(self, text: str) -> None: ...
    def on_content_delta(self, text: str) -> None: ...
    def on_assistant_end(self) -> None: ...
    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None: ...
    def on_tool_end(self, tool_call_id: str, result: str) -> None: ...
    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None: ...
    def on_usage(self, usage: Usage) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_interrupted(self) -> None: ...


class StdoutSink:
    """Headless fallback. Used when no TUI is running (tests, pipes)."""

    def on_turn_start(self, prompt: str) -> None:
        pass

    def on_turn_end(self, result: str) -> None:
        pass

    def on_user_message(self, text: str) -> None:
        print(f'> {text}')

    def on_reasoning_delta(self, text: str) -> None:
        print(f'\033[90m{text}\033[0m', end='', flush=True)

    def on_content_delta(self, text: str) -> None:
        print(text, end='', flush=True)

    def on_assistant_end(self) -> None:
        print()

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        print(f'  [tool] {name}({args_json})')

    def on_tool_end(self, tool_call_id: str, result: str) -> None:
        print(f'  [tool] -> {result}')

    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None:
        lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )

        for line in lines:
            if line.startswith('+') and not line.startswith('+++'):
                print(f'\033[32m{line}\033[0m', end='')
            elif line.startswith('-') and not line.startswith('---'):
                print(f'\033[31m{line}\033[0m', end='')
            else:
                print(line, end='')

    def on_usage(self, usage: Usage) -> None:
        pass

    def on_error(self, message: str) -> None:
        print(f'\033[31m[error] {message}\033[0m')

    def on_interrupted(self) -> None:
        print('\n[interrupted]')


class MultiSink:
    """Fan Sink events out to several downstream sinks.

    Each downstream call is isolated: a raising sink (e.g. Langfuse
    network blip) does not prevent the others from receiving the event.
    Sink calls happen on the worker thread and must never tear down the
    agent run because an observability backend is unhappy.
    """

    def __init__(self, sinks: list[Sink]) -> None:
        self.sinks = sinks

    def __getattr__(self, name: str):
        if not name.startswith('on_'):
            raise AttributeError(name)

        def fanout(*args, **kwargs) -> None:
            for s in self.sinks:
                try:
                    getattr(s, name)(*args, **kwargs)

                except Exception as e:
                    log.warning('sink %s.%s raised: %s', type(s).__name__, name, e)

        return fanout
