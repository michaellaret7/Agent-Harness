"""Executes parsed tool calls against the agent's registered tool functions.

The handler does one thing: take parsed tool_calls from the model, run the
matching Python callables, push start/end events to the Sink, and return
tool-result messages. Registration lives on the Agent.

For `EditFile` / `WriteFile` calls the handler snapshots the target file
before and after the call and emits a `on_file_diff` event so the UI can
render an inline highlighted diff. Tool functions themselves stay pure —
the LLM's tool-result message is unchanged.

Cancellation: between tool calls, checks `cancel_event`. Tool calls
in-flight are not killed (Python sync code can't be interrupted), but
remaining calls in the batch are skipped and synthesized as interrupted.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.messages import tool_msg
from agent.sinks import Sink

if TYPE_CHECKING:
    from agent.agent import Agent

#     ================================
# --> Helper funcs
#     ================================


def _snapshot(path: Path) -> str:
    """Read file contents, returning '' if the file doesn't exist yet."""
    if not path.is_file():
        return ''

    try:
        return path.read_text(encoding='utf-8')

    except OSError:
        return ''

#     ================================
# --> Handler
#     ================================


class ToolHandler:
    def __init__(self, agent: 'Agent') -> None:
        self.agent = agent

    def execute(
        self,
        tool_calls: list[dict],
        sink: Sink,
        cancel_event: threading.Event,
    ) -> list[dict[str, Any]]:
        """Run each tool call and return the corresponding tool-result messages."""

        messages: list[dict[str, Any]] = []

        for tc in tool_calls:
            tool_call_id = tc['id']
            name = tc['function']['name']
            args = tc['function']['arguments']

            # Send tool call start event to the sink
            sink.on_tool_start(tool_call_id, name, args) 

            if cancel_event.is_set():
                result = '[interrupted]'
            else:
                result = self.call_tool(name, args, tool_call_id, sink) # This is where the actual tool func is executed

            sink.on_tool_end(tool_call_id, result)

            messages.append(tool_msg(tool_call_id, result))

        return messages

    def call_tool(
        self,
        name: str,
        arguments_json: str,
        tool_call_id: str,
        sink: Sink,
    ) -> str:
        """Run one tool call end-to-end and emit a file-diff event if applicable."""
        try:
            kwargs = json.loads(arguments_json) if arguments_json else {}

        except (ValueError, TypeError) as e:
            return f'error: bad arguments JSON: {e}'

        target: Path | None = None

        if name in ('EditFile', 'WriteFile'):
            raw = kwargs.get('file_path')

            if isinstance(raw, str) and raw:
                target = Path(raw).expanduser().resolve()

        before = _snapshot(target) if target else ''

        result = self._invoke(name, kwargs)

        if target is None or result.startswith('error:'):
            return result

        after = _snapshot(target)

        if after != before:
            sink.on_file_diff(tool_call_id, str(target), before, after)

        return result

    def _invoke(self, name: str, kwargs: dict) -> str:
        """Look up the registered function and run it with exception wrapping."""
        fn = self.agent.tool_functions.get(name)

        if fn is None:
            return f'error: unknown tool {name!r}'

        try:
            return str(fn(**kwargs))

        except Exception as e:
            return f'error: {type(e).__name__}: {e}'
