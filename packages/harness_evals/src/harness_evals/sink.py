"""Record usage, stop reason, tool outcomes, duration, and errors from Sink events.

EvalSink exposes frozen RunMetadata through RunResult for graders; it does not
grade or duplicate the conversation in agent.messages.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent_harness.sinks.base import BaseSink, ToolOutcome
from agent_harness.usage import Usage


#     ================================
# --> RunMetadata
#     ================================


@dataclass(frozen=True)
class RunMetadata:
    """Snapshot of one agent run, taken after `Agent.run` returns."""

    usage: Usage                                   # summed over every LLM call in the run
    llm_calls: int
    stop_reason: str                               # 'answer_ready' | 'max_iterations' | 'cancelled' | '' (crashed)
    iterations: int
    tool_outcomes: tuple[tuple[str, ToolOutcome], ...]   # (tool name, outcome) in dispatch order
    errors: tuple[str, ...]
    interrupted: bool


#     ================================
# --> EvalSink
#     ================================


class EvalSink(BaseSink):
    """Accumulate loop events; expose them as `RunMetadata` via `.meta`."""

    def __init__(self) -> None:
        self.usage = Usage.zero()
        self.llm_calls = 0
        self.stop_reason = ''
        self.iterations = 0
        self.tool_outcomes: list[tuple[str, ToolOutcome]] = []
        self.errors: list[str] = []
        self.interrupted = False

        # tool_call_id -> tool name, so on_tool_end can label the outcome.
        self._tool_names: dict[str, str] = {}

    def on_usage(self, usage: Usage) -> None:
        # Fires once per LLM call, not once per run — accumulate.
        self.usage = self.usage + usage
        self.llm_calls += 1

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        self._tool_names[tool_call_id] = name

    def on_tool_end(self, tool_call_id: str, outcome: ToolOutcome) -> None:
        name = self._tool_names.get(tool_call_id, tool_call_id)

        self.tool_outcomes.append((name, outcome))

    def on_loop_end(self, stop_reason: str, iterations: int) -> None:
        self.stop_reason = stop_reason
        self.iterations = iterations

    def on_error(self, message: str) -> None:
        self.errors.append(message)

    def on_interrupted(self) -> None:
        self.interrupted = True

    @property
    def meta(self) -> RunMetadata:
        return RunMetadata(
            usage=self.usage,
            llm_calls=self.llm_calls,
            stop_reason=self.stop_reason,
            iterations=self.iterations,
            tool_outcomes=tuple(self.tool_outcomes),
            errors=tuple(self.errors),
            interrupted=self.interrupted,
        )
