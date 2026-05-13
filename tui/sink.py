"""Sink Protocol — the seam between the agent loop and the UI.

The loop calls these methods from a worker thread. TUISink mutates the
shared History and signals the prompt_toolkit app to repaint. StdoutSink is
the legacy fallback that prints exactly like the pre-TUI loop did.
"""
from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from prompt_toolkit.application import Application

    from tui.history import History


class Sink(Protocol):
    def on_user_message(self, text: str) -> None: ...
    def on_reasoning_delta(self, text: str) -> None: ...
    def on_content_delta(self, text: str) -> None: ...
    def on_assistant_end(self) -> None: ...
    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None: ...
    def on_tool_end(self, tool_call_id: str, result: str) -> None: ...
    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_interrupted(self) -> None: ...


class TUISink:
    def __init__(self, history: 'History', app: 'Application') -> None:
        self.history = history
        self.app = app

    def _invalidate(self) -> None:
        # Application.invalidate() is documented thread-safe.
        self.app.invalidate()

    def on_user_message(self, text: str) -> None:
        self.history.append_user(text)
        self._invalidate()

    def on_reasoning_delta(self, text: str) -> None:
        self.history.append_reasoning(text)
        self._invalidate()

    def on_content_delta(self, text: str) -> None:
        self.history.append_content(text)
        self._invalidate()

    def on_assistant_end(self) -> None:
        self.history.end_assistant()
        self._invalidate()

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        self.history.append_tool_start(tool_call_id, name, args_json)
        self._invalidate()

    def on_tool_end(self, tool_call_id: str, result: str) -> None:
        self.history.update_tool_result(tool_call_id, result)
        self._invalidate()

    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None:
        self.history.append_file_diff(tool_call_id, path, before, after)
        self._invalidate()

    def on_error(self, message: str) -> None:
        self.history.append_error(message)
        self._invalidate()

    def on_interrupted(self) -> None:
        self.history.mark_assistant_interrupted()
        self._invalidate()


class StdoutSink:
    """Legacy fallback. Used when no TUI is running (tests, pipes)."""

    def on_user_message(self, text: str) -> None:
        print(f'> {text}')

    def on_reasoning_delta(self, text: str) -> None:
        print(f'\033[90m{text}\033[0m', end='', flush=True)

    def on_content_delta(self, text: str) -> None:
        print(text, end='', flush=True)

    def on_assistant_end(self) -> None:
        print()

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        print(f'  [tool] {name}({args_json})')

    def on_tool_end(self, tool_call_id: str, result: str) -> None:
        print(f'  [tool] -> {result}')

    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None:
        lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )

        for line in lines:
            if line.startswith('+') and not line.startswith('+++'):
                print(f'\033[32m{line}\033[0m', end='')
            elif line.startswith('-') and not line.startswith('---'):
                print(f'\033[31m{line}\033[0m', end='')
            else:
                print(line, end='')

    def on_error(self, message: str) -> None:
        print(f'\033[31m[error] {message}\033[0m')

    def on_interrupted(self) -> None:
        print('\n[interrupted]')
