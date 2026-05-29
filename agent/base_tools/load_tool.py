"""LoadTool: fetch the full schema for one or more deferred tools.

Deferred tools appear in the model's tool list with a one-sentence
description and an empty parameter schema (see `Agent.add_tool`). To call
one, the model must first invoke this tool with the tool name(s) — the
returned text gives the full description and parameter schema so the
model can produce correct arguments on the next turn.

The deferred-tool registry is captured at `Agent.__init__` time (it depends
on which tools have been registered) and injected into the hidden
`_deferred_tools` / `_loaded_deferred` params via `bind_tool` (see `agent.py`).
Those underscore-prefixed params are hidden from the generated JSON Schema by
the decorator's convention, so the LLM never sees them.
"""
from __future__ import annotations

import json
from typing import Annotated, Any

from agent.decorator import Param, agent_tool


@agent_tool(name='LoadTool')
def load_tool(
    names: Annotated[list[str], Param(description='Tool names to load full schemas for.')],
    _deferred_tools: dict[str, dict[str, Any]] | None = None,
    _loaded_deferred: set[str] | None = None,
) -> str:
    """
    Load the full schema(s) for one or more deferred tools. Use this whenever
    you need to call a tool whose description ends with ` [deferred]` — the
    truncated stub in the tool list does not include parameter info, so you
    must load the full schema before invoking it. Pass a list of tool names;
    returns each tool's full description and JSON Schema as text. Once a
    schema is returned, the tool can be called normally for the rest of the
    conversation.
    """
    registry = _deferred_tools or {}
    loaded = _loaded_deferred if _loaded_deferred is not None else set()

    if not names:
        return 'error: provide at least one tool name'

    blocks: list[str] = []

    for name in names:
        match = registry.get(name)

        if match is None:
            available = ', '.join(sorted(registry)) or '(none)'
            blocks.append(f'error: unknown deferred tool {name!r}. Available: {available}')
            continue

        schema = {
            'name': match['name'],
            'description': match['description'],
            'parameters': match['parameters'],
        }

        loaded.add(name)
        blocks.append(f'Schema for {name!r}:\n{json.dumps(schema, indent=2)}')

    return '\n\n'.join(blocks)
