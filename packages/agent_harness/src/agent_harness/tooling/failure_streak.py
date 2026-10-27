"""Tracks consecutive tool failures so a run can block a failing tool, then stop.

The prompt rule "never retry the identical call" is easy to get around: a
model that changes one argument per retry (a fresh `client_order_id`) makes
every call technically new. So streaks are keyed on tool name only.

    error ──► streak += 1 ──► streak == MAX_TOOL_FAILURES ──► tool blocked
    ok    ──► streak = 0
    denied / interrupted ──► no change (the tool body never ran)

    call to a blocked tool ──► denied += 1 ──► denied == MAX_TOOL_FAILURES ──► `report` is set

Tools in `UNTRACKED_TOOLS` are never counted or blocked: their errors are
part of normal iteration, not a sign the tool is broken.

Owned by `ToolHandler`. Thread-safe, because safe-parallel tools record from
pool threads.
"""
from __future__ import annotations

import threading

from agent_harness.base_tools.submit_result import SUBMIT_RESULT
from agent_harness.sinks.base import ToolStatus

# Consecutive failures that block a tool, and calls to a blocked tool that end the run
MAX_TOOL_FAILURES = 5

# ExecuteCode errors are the model's own code failing while it debugs; SubmitResult
# errors are verifier rejections; both are bounded only by the run's max_iters
UNTRACKED_TOOLS = frozenset({'ExecuteCode', SUBMIT_RESULT})


class FailureStreaks:
    """Per-tool consecutive-failure counts, the blocks they trigger, and the stop report."""

    def __init__(self) -> None:
        self._streaks: dict[str, int] = {}
        self._last_errors: dict[str, str] = {}
        self._denied: dict[str, int] = {}
        self._report: str | None = None
        self._lock = threading.Lock()

    def record(self, name: str, status: ToolStatus, payload: str) -> bool:
        """Update `name`'s streak from one result. Returns True when this result blocks the tool."""
        if name in UNTRACKED_TOOLS:
            return False

        with self._lock:
            if status == 'ok':
                self._streaks[name] = 0

                return False

            if status != 'error':
                return False

            self._streaks[name] = self._streaks.get(name, 0) + 1
            self._last_errors[name] = payload

            return self._streaks[name] == MAX_TOOL_FAILURES

    def block_reason(self, name: str) -> str | None:
        """Return the deny message if `name` is blocked, counting the attempt; else None."""
        with self._lock:
            if self._streaks.get(name, 0) < MAX_TOOL_FAILURES:
                return None

            self._denied[name] = self._denied.get(name, 0) + 1
            last_error = self._last_errors[name]

            if self._denied[name] >= MAX_TOOL_FAILURES and self._report is None:
                self._report = (
                    f'Stopped by the agent harness: {name} failed {MAX_TOOL_FAILURES} times in a row and was called '
                    f'{MAX_TOOL_FAILURES} more times while blocked. Last error: {last_error}'
                )

        return (
            f'Blocked by the agent harness: {name} failed {MAX_TOOL_FAILURES} times in a row '
            f'(last error: {last_error}). Do not retry it or ask another agent to run it. '
            f'Use a different approach if one exists; otherwise end your turn and state '
            f'the problem in your final answer.'
        )

    @property
    def report(self) -> str | None:
        """The stop report once a blocked tool has been called MAX_TOOL_FAILURES times; None until then."""
        with self._lock:
            return self._report

    def reset(self) -> None:
        """Forget every streak, block, and report."""
        with self._lock:
            self._streaks.clear()
            self._last_errors.clear()
            self._denied.clear()
            self._report = None
