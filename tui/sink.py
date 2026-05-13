"""TUISink — Sink implementation that mutates the prompt_toolkit History.

The agent loop calls these methods from a worker thread. TUISink writes
into the shared History (lock-protected) and signals the prompt_toolkit
app to repaint. `Application.invalidate()` is documented thread-safe.

The Sink Protocol and the headless StdoutSink live in `agent/sink.py`
— this file is for TUI-specific implementation only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prompt_toolkit.application import Application

    from tui.history import History


class TUISink:
    def __init__(self, history: 'History', app: 'Application') -> None:
        self.history = history
        self.app = app

    def _invalidate(self) -> None:
        self.app.invalidate()

    def on_turn_start(self, prompt: str) -> None:
        pass

    def on_turn_end(self, result: str) -> None:
        pass

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
