"""Sink Protocol — the agent's output contract.

The agent loop calls these methods from a worker thread. Implementations
live wherever they make sense:
- `StdoutSink` (in `agent_harness/sinks/stdout.py`) is the headless fallback for
  tests/scripts that import `Agent` without a TUI.
- `MultiSink` (here) fans events out to several downstream sinks.
- `TUISink` lives in `tui/sink.py` and mutates the prompt_toolkit History.
- `LangfuseSink` lives in `agent_harness/sinks/langfuse.py` and mirrors events to
  Langfuse spans.

`on_turn_start` / `on_turn_end` bracket one call to `Agent.run`. They are
emitted by Agent.run itself, not the execution loop, so adding new
observability sinks does not require touching agent_harness/loop.py.

`on_loop_start` / `on_loop_end` and `on_iteration_start` / `on_iteration_end`
bracket the execution loop and each iteration through it. They give
trace-emitting sinks the boundaries they need to build a nested
hierarchy (`turn > loop > iteration > {tool, generation}`). UI sinks
typically no-op these.

`BaseSink` supplies no-op defaults for every event so concrete sinks only
override what they care about. The `Sink` Protocol stays the structural
typing contract used in signatures; `BaseSink` is the inheritance hook.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from agent_harness.usage import Usage

log = logging.getLogger(__name__)


#     ================================
# --> ToolOutcome
#     ================================


ToolStatus = Literal['ok', 'error', 'interrupted']


@dataclass(frozen=True)
class ToolOutcome:
    """The terminal state of one tool dispatch.

    Carried by `on_tool_end`. `payload` is the LLM-facing string (unchanged
    from the previous `result` argument); `status` and `duration` move the
    timing and outcome classification out of every sink and into the one
    place that has the information — `ToolHandler._run_tool`.
    """

    payload: str
    status: ToolStatus
    duration: float


#     ================================
# --> Sink Protocol
#     ================================


class Sink(Protocol):
    def on_turn_start(self, task: str) -> None: ...
    def on_turn_end(self, result: str) -> None: ...
    def on_loop_start(self, model: str, max_iters: int, tool_names: list[str]) -> None: ...
    def on_loop_end(self, stop_reason: str, iterations: int) -> None: ...
    def on_iteration_start(self, number: int, message_count: int) -> None: ...
    def on_iteration_end(self, number: int, action: str, content: str, tools_called: list[str]) -> None: ...
    def on_user_message(self, text: str) -> None: ...
    def on_reasoning_delta(self, text: str) -> None: ...
    def on_content_delta(self, text: str) -> None: ...
    def on_assistant_end(self) -> None: ...
    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None: ...
    def on_tool_end(self, tool_call_id: str, outcome: ToolOutcome) -> None: ...
    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None: ...
    def on_plan_update(self, plan: list[dict]) -> None: ...
    def on_usage(self, usage: Usage) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_interrupted(self) -> None: ...


#     ================================
# --> BaseSink
#     ================================


class BaseSink:
    """No-op default implementations for every Sink event.

    Concrete sinks subclass this and override only the events they care
    about. The Sink Protocol stays the structural typing contract; this
    class is the inheritance hook that removes the `pass` graveyard from
    every implementation.
    """

    def on_turn_start(self, task: str) -> None: pass
    def on_turn_end(self, result: str) -> None: pass
    def on_loop_start(self, model: str, max_iters: int, tool_names: list[str]) -> None: pass
    def on_loop_end(self, stop_reason: str, iterations: int) -> None: pass
    def on_iteration_start(self, number: int, message_count: int) -> None: pass
    def on_iteration_end(self, number: int, action: str, content: str, tools_called: list[str]) -> None: pass
    def on_user_message(self, text: str) -> None: pass
    def on_reasoning_delta(self, text: str) -> None: pass
    def on_content_delta(self, text: str) -> None: pass
    def on_assistant_end(self) -> None: pass
    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None: pass
    def on_tool_end(self, tool_call_id: str, outcome: ToolOutcome) -> None: pass
    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None: pass
    def on_plan_update(self, plan: list[dict]) -> None: pass
    def on_usage(self, usage: Usage) -> None: pass
    def on_error(self, message: str) -> None: pass
    def on_interrupted(self) -> None: pass


#     ================================
# --> MultiSink
#     ================================


class MultiSink:
    """Fan Sink events out to several downstream sinks.

    Each downstream call is isolated: a raising sink (e.g. Langfuse
    network blip) does not prevent the others from receiving the event.
    Sink calls happen on the worker thread and must never tear down the
    agent run because an observability backend is unhappy.
    """

    def __init__(self, sinks: list[Sink]) -> None:
        self.sinks = sinks

    def __getattr__(self, name: str):
        if not name.startswith('on_'):
            raise AttributeError(name)

        def fanout(*args, **kwargs) -> None:
            for s in self.sinks:
                try:
                    getattr(s, name)(*args, **kwargs)

                except Exception as e:
                    log.warning('sink %s.%s raised: %s', type(s).__name__, name, e)

        return fanout
