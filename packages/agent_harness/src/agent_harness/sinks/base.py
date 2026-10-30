"""Sink Protocol and default implementations for agent output events.

Agent.run emits turn boundaries; the loop emits loop and iteration boundaries:

    turn > loop > iteration > {tool, generation}

Callbacks run on worker threads. BaseSink supplies no-op defaults; MultiSink
fans events out to sinks such as StdoutSink, TUISink, and LangfuseSink.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agent_harness.usage import Usage

log = logging.getLogger(__name__)


#     ================================
# --> ToolOutcome
#     ================================


ToolStatus = Literal['ok', 'error', 'interrupted', 'denied']


@dataclass(frozen=True)
class ToolOutcome:
    """The terminal state of one tool dispatch.

    Carried by `on_tool_end`. `payload` is the LLM-facing string (unchanged
    from the previous `result` argument); `status` and `duration` move the
    timing and outcome classification out of every sink and into the one
    place that has the information — `ToolHandler._run_tool`. `data` is the
    tool's structured `ToolResult.data`, None when it set none or never ran.
    """

    payload: str
    status: ToolStatus
    duration: float
    data: Any = None


#     ================================
# --> CompactionEvent
#     ================================


@dataclass(frozen=True)
class CompactionEvent:
    """Context compaction details carried by `on_compaction`.

    Token counts are estimates; `segment` is the archive path, if any.
    `summarized=False` means summarization failed and history retained only an
    archive pointer. This recoverable state does not emit `on_error`.
    """

    tokens_before: int
    tokens_after: int
    archived_messages: int
    segment: str | None
    summarized: bool


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
    def on_plan_update(self, plan: list[dict]) -> None: ...
    def on_usage(self, usage: Usage) -> None: ...
    def on_compaction(self, event: CompactionEvent) -> None: ...
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
    def on_plan_update(self, plan: list[dict]) -> None: pass
    def on_usage(self, usage: Usage) -> None: pass
    def on_compaction(self, event: CompactionEvent) -> None: pass
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
