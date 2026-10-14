"""ToolContext: the run a tool call belongs to, handed to tools that ask for it.

A tool opts in by declaring a hidden `_ctx` parameter; `ToolHandler` fills it
on every call. Underscore params are absent from the schema and the handler
rejects model-supplied ones, so the model can neither see nor forge it.

`bind_tool` and `_ctx` split the work: `bind_tool` injects what the
application chose when building the tool (a sandbox, a store, a registry);
`_ctx` carries the run itself (the agent, its sink, its cancel signal).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_harness.sinks.base import Sink

if TYPE_CHECKING:
    from agent_harness.agent import Agent


# The hidden parameter a tool declares to receive its ToolContext
CONTEXT_PARAM = '_ctx'


@dataclass(frozen=True)
class ToolContext:
    """The live agent plus the sink and cancel event of the current `run()`."""

    agent: 'Agent'
    sink: Sink
    cancel_event: threading.Event
