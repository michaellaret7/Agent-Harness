"""HookContext provides a common payload for lifecycle hooks.

Register callbacks with `agent.add_hook(event, fn)`. Hooks run synchronously;
return values are ignored. HookSink translates Sink events, leaving unrelated
fields as None. The live `agent` reference exposes additional run state.
This module has no runtime dependencies on the loop or sinks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    from agent_harness.agent import Agent
    from agent_harness.sinks.base import ToolOutcome


HookEvent = Literal[
    'turn_start',
    'turn_end',

    'loop_start',
    'loop_end',

    'iteration_start',
    'iteration_end',
    
    'tool_start',
    'tool_end',
]


@dataclass(frozen=True)
class HookContext:
    """A normalized snapshot of one lifecycle event handed to every hook.

    `agent` is a live reference (not a copy) — hooks that enrich context
    append to `agent.messages` through it. Do that only at start
    boundaries (`turn_start` / `loop_start` / `iteration_start`), where the
    message history is well-formed and no tool_call awaits its result.
    """

    event: str
    agent: 'Agent'
    tool_name: str | None = None
    tool_call_id: str | None = None
    args: dict | None = None
    outcome: 'ToolOutcome | None' = None
    # Event-specific payload the live agent can't supply (e.g. an iteration's
    # action/content/tools_called, which aren't yet in agent.messages when the
    # event fires). None on events that carry nothing extra.
    detail: dict | None = None


Hook = Callable[[HookContext], None]
