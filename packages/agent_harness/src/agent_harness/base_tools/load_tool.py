"""LoadTool: fetch the full schema for one or more deferred tools.

Deferred tools appear in the model's tool list with a one-sentence
description and an empty parameter schema (see `Agent.add_tool`). To call
one, the model must first invoke this tool with the tool name(s) — loading
promotes the stub entry in the API tool list to the full schema in place,
and returns the full description and parameter schema as text so the
model can produce correct arguments on the next turn.

The deferred-tool registry is captured at `Agent.__init__` time (it depends
on which tools have been registered) and injected into the hidden
`_deferred_tools` / `_api_tools` params via `bind_tool` (see `agent.py`).
Those underscore-prefixed params are hidden from the generated JSON Schema by
the decorator's convention, so the LLM never sees them.

Loading pops the tool out of the registry — "still deferred" simply means
"still in `agent.deferred_tools`"; there is no separate loaded-set.
"""
from __future__ import annotations

import json
from typing import Annotated, Any

from agent_harness.decorator import Param, agent_tool


@agent_tool(name='LoadTool')
def load_tool(
    names: Annotated[list[str], Param(description='Tool names to load full schemas for.')],
    _deferred_tools: dict[str, dict[str, Any]] | None = None,
    _api_tools: list[dict[str, Any]] | None = None,
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
    registry = _deferred_tools if _deferred_tools is not None else {}
    api_tools = _api_tools if _api_tools is not None else []

    if not names:
        return 'error: provide at least one tool name'

    blocks: list[str] = []

    for name in names:
        match = registry.pop(name, None)

        if match is None:
            if any(entry['function']['name'] == name for entry in api_tools):
                blocks.append(f'{name!r} is already loaded — call it directly.')
            else:
                available = ', '.join(sorted(registry)) or '(none)'
                blocks.append(f'error: unknown deferred tool {name!r}. Available: {available}')

            continue

        # Promote the stub entry in the API tool list to the full schema, in
        # place. The list is shared by reference with the execution loop, so
        # the next LLM call declares the real parameters — schema-strict
        # providers constrain tool-call arguments to the declared schema, and
        # would otherwise force an empty arguments object.
        for entry in api_tools:
            if entry['function']['name'] == name:
                entry['function']['description'] = match['description']
                entry['function']['parameters'] = match['parameters']
                break

        schema = {
            'name': match['name'],
            'description': match['description'],
            'parameters': match['parameters'],
        }

        blocks.append(f'Schema for {name!r}:\n{json.dumps(schema, indent=2)}')

    return '\n\n'.join(blocks)
