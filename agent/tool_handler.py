"""Executes parsed tool calls against the agent's registered tool functions.

The handler does one thing: take parsed tool_calls from the model, run the
matching Python callables, push start/end events to the Sink, and return
tool-result messages. Registration lives on the Agent.

Cancellation: between tool calls, checks `cancel_event`. Tool calls
in-flight are not killed (Python sync code can't be interrupted), but
remaining calls in the batch are skipped and synthesized as interrupted.
"""
from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

from tui.sink import Sink

if TYPE_CHECKING:
    from agent.agent import Agent


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

            sink.on_tool_start(tool_call_id, name, args)

            if cancel_event.is_set():
                result = '[interrupted]'
            else:
                result = self.call_tool(name, args)

            sink.on_tool_end(tool_call_id, result)

            messages.append({
                'role': 'tool',
                'tool_call_id': tool_call_id,
                'content': result,
            })

        return messages

    def call_tool(self, name: str, arguments_json: str) -> str:
        fn = self.agent.tool_functions.get(name)

        if fn is None:
            return f'error: unknown tool {name!r}'

        try:
            kwargs = json.loads(arguments_json) if arguments_json else {}
            return str(fn(**kwargs))

        except Exception as e:
            return f'error: {type(e).__name__}: {e}'
