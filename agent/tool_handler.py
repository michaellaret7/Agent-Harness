"""Tool registration and dispatch.

A tool is a (name, description, JSON-Schema parameters, callable). The handler
exposes schemas for the model and invokes the matching callable when the model
emits a tool call.
"""
from __future__ import annotations

import json
from typing import Any


class ToolHandler:
    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}

    def register(self, tool: dict[str, Any]) -> None:
        """Register a tool dict with keys: name, description, parameters, fn."""
        name = tool['name']
        self._tools[name] = {
            'schema': {
                'type': 'function',
                'function': {
                    'name': name,
                    'description': tool['description'],
                    'parameters': tool['parameters'],
                },
            },
            'fn': tool['fn'],
        }

    def schemas(self) -> list[dict[str, Any]]:
        return [t['schema'] for t in self._tools.values()]

    def call(self, name: str, arguments_json: str) -> str:
        if name not in self._tools:
            return f'error: unknown tool {name!r}'
        try:
            kwargs = json.loads(arguments_json) if arguments_json else {}
            return str(self._tools[name]['fn'](**kwargs))
        except Exception as e:
            return f'error: {type(e).__name__}: {e}'
