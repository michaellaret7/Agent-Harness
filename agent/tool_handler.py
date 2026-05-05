"""Executes parsed tool calls against the agent's registered tool functions.

The handler does one thing: take parsed tool_calls from the model, run the
matching Python callables, and return tool-result messages. Registration
lives on the Agent.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.agent import Agent


class ToolHandler:
    def __init__(self, agent: 'Agent') -> None:
        self.agent = agent

    def execute(self, tool_calls: list[dict]) -> list[dict[str, Any]]:
        """Run each tool call and return the corresponding tool-result messages."""
        messages: list[dict[str, Any]] = []

        for tc in tool_calls:
            name = tc['function']['name']
            args = tc['function']['arguments']

            result = self.call_tool(name, args)

            print(f'  [tool] {name}({args}) -> {result}')

            messages.append({
                'role': 'tool',
                'tool_call_id': tc['id'],
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
