"""HookSink — drives user-registered hooks off the Sink stream.

Rides the ambient sink chain alongside StdoutSink/TUISink/LangfuseSink.
Each Sink event it cares about is translated into a uniform `HookContext`
(see `agent.hooks`) and fanned out to the callbacks registered on
`agent.hooks` for that event.

Two deliberate choices mirror `MultiSink`:
- Hooks run synchronously on the worker thread, so the loop waits for them.
  A hook that must not block the loop spawns its own thread and returns.
- A raising hook is logged and swallowed — a hook must never tear
  down the agent run.

The raw `on_tool_end` event carries no tool name (only id + outcome), so
the sink records `id -> (name, args)` at `on_tool_start` and recovers it at
`on_tool_end`. By the time a `tool_end` hook runs, `ctx.tool_name` is set.

High-frequency streaming events (`on_content_delta`, `on_reasoning_delta`)
are intentionally NOT exposed as hooks: they fire per-token and are not
what hooks are for.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from agent.hooks import HookContext
from agent.sinks.protocol import BaseSink, ToolOutcome

if TYPE_CHECKING:
    from agent.agent import Agent

log = logging.getLogger(__name__)

#     ================================
# --> Helper funcs
#     ================================


def _parse_args(args_json: str) -> dict:
    """Parse tool-call arguments, returning {} on empty or malformed JSON."""
    if not args_json:
        return {}

    try:
        parsed = json.loads(args_json)

    except (ValueError, TypeError):
        return {}

    return parsed if isinstance(parsed, dict) else {}

#     ================================
# --> HookSink
#     ================================


class HookSink(BaseSink):
    """Translates Sink events into HookContext and fans out to observe hooks."""

    def __init__(self, agent: 'Agent') -> None:
        self.agent = agent
        # Correlates tool_start -> tool_end so tool_end hooks recover the name/args.
        self._inflight: dict[str, tuple[str, dict]] = {}

    def _fire(
        self,
        event: str,
        *,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        args: dict | None = None,
        outcome: ToolOutcome | None = None,
    ) -> None:
        """Build the context and run every hook registered for this event."""

        # Get the hooks from the agent class self.agent.hooks
        hooks = self.agent.hooks.get(event)

        if not hooks:
            return

        # Pass the agent class object to the Hook Context data class 
        ctx = HookContext(
            event, 
            self.agent, 
            tool_name, 
            tool_call_id, 
            args, 
            outcome
        )

        # Iterate through the hooks and run the hook functions
        for target, fn in hooks:
            # `tool=` filters tool events to a set of tool names; it matches
            # nothing on non-tool events (tool_name is None there).
            if target is not None and tool_name not in target:
                continue
            
            # Execute the hook function with the context object passed to it
            try:
                fn(ctx)

            # If the hook function raises an exception, log the error
            except Exception as e:
                log.warning('hook %s for %s raised: %s', getattr(fn, '__name__', fn), event, e)

    #     ---- Lifecycle boundaries ----
    #     Sink params are ignored — boundary hooks read state off ctx.agent.

    def on_turn_start(self, task: str) -> None:
        self._fire('turn_start')

    def on_turn_end(self, result: str) -> None:
        self._fire('turn_end')

    def on_loop_start(self, model: str, max_iters: int, tool_names: list[str]) -> None:
        self._fire('loop_start')

    def on_loop_end(self, stop_reason: str, iterations: int) -> None:
        self._fire('loop_end')

    def on_iteration_start(self, number: int, message_count: int) -> None:
        self._fire('iteration_start')

    def on_iteration_end(self, number: int, action: str, content: str, tools_called: list[str]) -> None:
        self._fire('iteration_end')

    #     ---- Tool events ----

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        args = _parse_args(args_json)
        self._inflight[tool_call_id] = (name, args)

        self._fire('tool_start', tool_name=name, tool_call_id=tool_call_id, args=args)

    def on_tool_end(self, tool_call_id: str, outcome: ToolOutcome) -> None:
        name, args = self._inflight.pop(tool_call_id, (None, None))

        self._fire('tool_end', tool_name=name, tool_call_id=tool_call_id, args=args, outcome=outcome)
