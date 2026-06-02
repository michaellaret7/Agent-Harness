"""Gate primitives — allow, deny, or rewrite a tool call.

A gate is a callable registered via `agent.add_gate(fn, tool=...)`. It
fires in `ToolHandler._dispatch` after args are parsed but before the tool
runs, and returns a `GateVerdict` that the loop consumes: `allow`, `deny`,
or `rewrite` (run with substituted args). Unlike an observer hook, a gate's
return value has authority over what the loop does — use it to stop or
alter a call (permissions, sandboxing, limits, kill switches).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    from agent_harness.agent import Agent


GateDecision = Literal['allow', 'deny', 'rewrite']


@dataclass(frozen=True)
class GateContext:
    """The tool call presented to a gate, just before it would run.

    `agent` is a live reference (not a copy). `args` is the parsed kwargs
    the tool would receive — a gate inspects these to reach its verdict but
    must not mutate them in place; to change them it returns
    `GateVerdict.rewrite(new_args)`.
    """

    tool_name: str
    tool_call_id: str
    args: dict
    agent: 'Agent'


@dataclass(frozen=True)
class GateVerdict:
    """A gate's decision about one tool call.

    Build one through the factory methods rather than the constructor, so
    the decision and its payload can never disagree:
    - `allow()` — let the call run unchanged.
    - `deny(reason)` — block the call; `reason` becomes the tool result.
    - `rewrite(args)` — run the call with `args` in place of the original.
    """

    decision: GateDecision
    reason: str | None = None
    args: dict | None = None

    @classmethod
    def allow(cls) -> 'GateVerdict':
        return cls('allow')

    @classmethod
    def deny(cls, reason: str) -> 'GateVerdict':
        return cls('deny', reason=reason)

    @classmethod
    def rewrite(cls, args: dict) -> 'GateVerdict':
        return cls('rewrite', args=args)


Gate = Callable[[GateContext], GateVerdict]
