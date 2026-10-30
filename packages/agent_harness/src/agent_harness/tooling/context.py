"""ToolContext carries the agent, sink, and cancellation signal for a tool call.

Tools opt in with a hidden `_ctx` parameter, filled by ToolHandler and rejected
if supplied by the model. `bind_tool` supplies construction-time dependencies;
`_ctx` supplies the current run.
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
