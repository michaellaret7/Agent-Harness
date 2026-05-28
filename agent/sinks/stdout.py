"""StdoutSink — headless Sink implementation.

Used when no TUI is running (tests, scripts, pipelines). Renders the
assistant's reasoning/content stream, tool calls, file diffs, and errors
to stdout with ANSI styling, grouping starts/ends of consecutive tool
blocks into paired clusters for readability.
"""
from __future__ import annotations

import difflib
import time

from agent.sinks.helpers import format_args_inline, format_tool_summary
from agent.usage import Usage


#     ================================
# --> ANSI escape codes
#     ================================


_GRAY = '\033[90m'
_CYAN = '\033[36m'
_YELLOW = '\033[33m'
_BOLD = '\033[1m'
_RED = '\033[31m'
_GREEN = '\033[32m'
_RESET = '\033[0m'


#     ================================
# --> StdoutSink
#     ================================


class StdoutSink:
    """Headless fallback. Used when no TUI is running (tests, pipes)."""

    def __init__(self) -> None:
        # Tracks which assistant text stream (reasoning vs content) is
        # currently being emitted so transitions can be cleanly separated.
        self._stream_state: str = 'idle'  # 'idle' | 'reasoning' | 'content'

        # Tracks tool-block phase so starts and ends render as paired
        # groups: a cluster of starts, blank line, then a cluster of ends.
        self._tool_phase: str | None = None  # None | 'starts' | 'ends'

        # Per-call start timestamps so on_tool_end can report wall-clock duration.
        self._tool_start_times: dict[str, float] = {}

    def _enter_stream(self, new_state: str, label: str) -> None:
        """Print a section header, closing any prior block first.

        Each section gets a single blank line above its header; content
        sits directly under the header (no blank between).
        """
        # Any open tool block ends here — separate it from the new section.
        if self._tool_phase is not None:
            print()
            self._tool_phase = None

        if self._stream_state == new_state:
            return

        if self._stream_state != 'idle':
            print('\n')

        print(f'{_CYAN}{label}{_RESET}')
        self._stream_state = new_state

    def on_turn_start(self, task: str) -> None:
        pass

    def on_turn_end(self, result: str) -> None:
        pass

    def on_loop_start(self, model: str, max_iters: int, tool_names: list[str]) -> None:
        pass

    def on_loop_end(self, stop_reason: str, iterations: int) -> None:
        pass

    def on_iteration_start(self, number: int, message_count: int) -> None:
        pass

    def on_iteration_end(self, number: int, action: str, content: str, tools_called: list[str]) -> None:
        pass

    def on_user_message(self, text: str) -> None:
        print(f'\n{_BOLD}> {text}{_RESET}\n')

    def on_reasoning_delta(self, text: str) -> None:
        self._enter_stream('reasoning', '[thinking]')
        print(f'{_GRAY}{text}{_RESET}', end='', flush=True)

    def on_content_delta(self, text: str) -> None:
        self._enter_stream('content', '[answer]')
        print(text, end='', flush=True)

    def on_assistant_end(self) -> None:
        if self._stream_state != 'idle':
            print('\n')

        self._stream_state = 'idle'

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        self._tool_start_times[tool_call_id] = time.perf_counter()

        # If a previous ends-cluster was just emitted, separate the new
        # chunk visually before starting the next round of starts.
        if self._tool_phase == 'ends':
            print()

        args = format_args_inline(args_json)
        print(f'  {_YELLOW}[tool]{_RESET} {name}({args})')
        self._tool_phase = 'starts'

    def on_tool_end(self, tool_call_id: str, result: str) -> None:
        start = self._tool_start_times.pop(tool_call_id, None)
        duration = time.perf_counter() - start if start is not None else 0.0

        # First end after a starts-cluster: blank line separates the two blocks.
        if self._tool_phase == 'starts':
            print()

        summary = format_tool_summary(result, duration)
        print(f'  {_YELLOW}->{_RESET} {summary}')
        self._tool_phase = 'ends'

    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None:
        lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )

        for line in lines:
            if line.startswith('+') and not line.startswith('+++'):
                print(f'{_GREEN}{line}{_RESET}', end='')

            elif line.startswith('-') and not line.startswith('---'):
                print(f'{_RED}{line}{_RESET}', end='')

            else:
                print(line, end='')

    def on_plan_update(self, plan: list[dict]) -> None:
        completed = sum(1 for i in plan if i.get('status') == 'completed')
        total = len(plan)
        print(f'  {_CYAN}[plan]{_RESET} {completed}/{total} completed')

    def on_usage(self, usage: Usage) -> None:
        pass

    def on_error(self, message: str) -> None:
        print(f'{_RED}[error] {message}{_RESET}')

    def on_interrupted(self) -> None:
        print('\n[interrupted]')
