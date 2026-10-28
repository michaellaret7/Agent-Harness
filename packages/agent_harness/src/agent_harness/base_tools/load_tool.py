"""Load full schemas for deferred tools.

Replace each stub in agent.tools with its full schema, return the schema as
text, and remove it from agent.deferred_tools. The hidden `_ctx` parameter
supplies the agent.
"""
from __future__ import annotations

import json
from typing import Annotated

from agent_harness.tooling.context import ToolContext
from agent_harness.tooling.decorator import Param, agent_tool
from agent_harness.tooling.result import ToolResult


@agent_tool(name='LoadTool')
def load_tool(
    names: Annotated[list[str], Param(description='Tool names to load full schemas for.')],
    _ctx: ToolContext | None = None,
) -> ToolResult:
    """
    Load the full schema(s) for one or more deferred tools. Use this whenever
    you need to call a tool whose description ends with ` [deferred]` — the
    truncated stub in the tool list does not include parameter info, so you
    must load the full schema before invoking it. Pass a list of tool names;
    returns each tool's full description and JSON Schema as text. Once a
    schema is returned, the tool can be called normally for the rest of the
    conversation.
    """
    if _ctx is None:
        return ToolResult('error: LoadTool called without a run context', status='error')

    registry = _ctx.agent.deferred_tools
    api_tools = _ctx.agent.tools

    if not names:
        return ToolResult('error: provide at least one tool name', status='error')

    blocks: list[str] = []
    failed = False

    for name in names:
        match = registry.pop(name, None)

        if match is None:
            if any(entry['function']['name'] == name for entry in api_tools):
                blocks.append(f'{name!r} is already loaded — call it directly.')
            else:
                failed = True
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

    return ToolResult('\n\n'.join(blocks), status='error' if failed else 'ok')
