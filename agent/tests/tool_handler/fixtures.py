"""Shared fakes and synthetic tools for ToolHandler tests.

The ToolHandler is the unit under test; the tools it dispatches are
synthetic so we can control timing, force errors, and observe execution
order without depending on filesystem state or network calls.

Exposed building blocks:
- `FakeAgent` — minimal stand-in carrying the three attributes the
  handler reaches into (`tool_functions`, `deferred_tools`,
  `loaded_deferred`).
- `RecordingSink` — captures `on_tool_start` / `on_tool_end` events in
  the order they arrive. Useful for asserting that sink events fire.
- `ExecutionLog` — thread-safe (tool_id, start_ts, end_ts) tape that
  synthetic tools write into. Used to verify temporal ordering across
  parallel chunks and serial barriers.
- `make_handler` — builds a ToolHandler wired to a FakeAgent populated
  with the given `{name: callable}` map.
- Synthetic tool factories: `make_fast_tool`, `make_slow_tool`,
  `make_logging_tool`, `make_error_tool`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.decorator import agent_tool
from agent.tool_handler import ToolHandler

#     ================================
# --> Helper funcs
#     ================================


def make_handler(tools: dict[str, Callable]) -> ToolHandler:
    """Build a ToolHandler bound to a FakeAgent with the given tools registered."""
    agent = FakeAgent(tool_functions=tools)

    return ToolHandler(agent)  # type: ignore[arg-type]


def tool_call(call_id: str, name: str, args_json: str = '{}') -> dict[str, Any]:
    """Build the dict shape the handler expects for one tool_call entry."""
    return {
        'id': call_id,
        'type': 'function',
        'function': {'name': name, 'arguments': args_json},
    }


#     ================================
# --> Test doubles
#     ================================


@dataclass
class FakeAgent:
    """Stand-in for the real Agent — carries only the attributes the handler reads."""

    tool_functions: dict[str, Callable]
    deferred_tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    loaded_deferred: set[str] = field(default_factory=set)


class RecordingSink:
    """Captures every sink event the handler emits, in arrival order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple]] = []
        self._lock = threading.Lock()

    def _record(self, name: str, *args: Any) -> None:
        with self._lock:
            self.events.append((name, args))

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        self._record('on_tool_start', tool_call_id, name, args_json)

    def on_tool_end(self, tool_call_id: str, result: str) -> None:
        self._record('on_tool_end', tool_call_id, result)

    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None:
        self._record('on_file_diff', tool_call_id, path, len(before), len(after))

    def starts(self) -> list[str]:
        """Return the tool_call_ids in the order on_tool_start fired."""
        return [args[0] for name, args in self.events if name == 'on_tool_start']

    def ends(self) -> list[str]:
        """Return the tool_call_ids in the order on_tool_end fired."""
        return [args[0] for name, args in self.events if name == 'on_tool_end']


class ExecutionLog:
    """Thread-safe (tool_id, start_ts, end_ts) tape for timing assertions."""

    def __init__(self) -> None:
        self._entries: list[tuple[str, float, float]] = []
        self._lock = threading.Lock()

    def record(self, tool_id: str, start: float, end: float) -> None:
        with self._lock:
            self._entries.append((tool_id, start, end))

    def entries(self) -> list[tuple[str, float, float]]:
        with self._lock:
            return list(self._entries)

    def window(self, tool_id: str) -> tuple[float, float]:
        """Return (start_ts, end_ts) for the named tool. Raises if not found."""
        for tid, start, end in self.entries():

            if tid == tool_id:
                return start, end

        raise AssertionError(f'no execution log entry for {tool_id!r}')


#     ================================
# --> Synthetic tool factories
#     ================================


def make_fast_tool(name: str, *, safe_parallel: bool) -> Callable:
    """Build a tool that immediately returns 'ok-<name>'."""

    @agent_tool(name=name, safe_parallel=safe_parallel)
    def _tool() -> str:
        """Fast no-op test tool."""
        return f'ok-{name}'

    return _tool


def make_slow_tool(name: str, *, safe_parallel: bool, sleep_s: float) -> Callable:
    """Build a tool that sleeps for `sleep_s` seconds and returns 'slow-<name>'."""

    @agent_tool(name=name, safe_parallel=safe_parallel)
    def _tool() -> str:
        """Slow test tool — used to verify parallelism via wall-clock time."""
        time.sleep(sleep_s)

        return f'slow-{name}'

    return _tool


def make_logging_tool(
    name: str,
    *,
    safe_parallel: bool,
    sleep_s: float,
    log: ExecutionLog,
) -> Callable:
    """Build a tool that records (id, start_ts, end_ts) into `log` and returns 'log-<name>'.

    The `tool_id` argument the LLM would pass is forwarded as `tid` so
    the recorded ID matches the tool_call id at the handler level. This
    lets temporal assertions reference the same id used in tool_call().
    """

    @agent_tool(name=name, safe_parallel=safe_parallel)
    def _tool(tid: str) -> str:
        """Logging test tool — writes its execution window to the shared log.

        Args:
            tid: identifier echoed back into the ExecutionLog for temporal assertions.
        """
        start = time.monotonic()
        time.sleep(sleep_s)
        end = time.monotonic()

        log.record(tid, start, end)

        return f'log-{name}-{tid}'

    return _tool


def make_error_tool(name: str, *, safe_parallel: bool) -> Callable:
    """Build a tool that raises a RuntimeError so we can verify error surfacing."""

    @agent_tool(name=name, safe_parallel=safe_parallel)
    def _tool() -> str:
        """Error test tool — always raises."""
        raise RuntimeError('boom')

    return _tool


def make_cancelling_tool(
    name: str,
    *,
    safe_parallel: bool,
    cancel_event: threading.Event,
) -> Callable:
    """Build a tool that sets `cancel_event` when called.

    Used to simulate the user hitting Esc mid-batch: dropping this tool
    between chunks proves the handler honors the cancel check at chunk
    boundaries and short-circuits every remaining call to `[interrupted]`.
    """

    @agent_tool(name=name, safe_parallel=safe_parallel)
    def _tool() -> str:
        """Cancelling test tool — sets the cancel_event and returns."""
        cancel_event.set()

        return f'cancelled-by-{name}'

    return _tool
