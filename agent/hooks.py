"""Hook primitives — the normalized payload passed to every observe hook.

A hook is a user-supplied callable registered on an Agent via
`agent.add_hook(event, fn)`. It fires on a lifecycle event (turn / loop /
iteration boundaries, tool start/end), receives a single `HookContext`,
and returns nothing — the agent loop ignores its return value and never
blocks on its verdict. Side effects only: log, publish an event, spawn a
thread/agent, or enrich `ctx.agent.messages`.

`HookContext` exists so every hook shares one signature regardless of
which event fired. The raw Sink events have wildly different shapes
(`on_tool_start(id, name, args_json)` vs `on_loop_start(model, ...)`); the
`HookSink` translates each into this uniform object. Fields not relevant
to an event stay None — `outcome` is only set on `tool_end`, `args`/
`tool_name` only on tool events. Everything else a hook needs is reachable
through the live `agent` reference.

This is a leaf module: it imports nothing from the loop or sinks at
runtime, so it can never participate in an import cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    from agent.agent import Agent
    from agent.sinks.base import ToolOutcome


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


Hook = Callable[[HookContext], None]
