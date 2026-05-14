"""TUISink — Sink implementation that mutates the prompt_toolkit History.

The agent loop calls these methods from a worker thread. TUISink writes
into the shared History (lock-protected) and signals the prompt_toolkit
app to repaint. `Application.invalidate()` is documented thread-safe.

The Sink Protocol and the headless StdoutSink live in `agent/sink.py`
— this file is for TUI-specific implementation only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent.usage import Usage

if TYPE_CHECKING:
    from prompt_toolkit.application import Application

    from tui.history import History


class TUISink:
    def __init__(self, history: 'History', app: 'Application') -> None:
        self.history = history
        self.app = app

        # Usage telemetry. `last_call_usage` is per-LLM-call (so the
        # status bar shows the *current* context size, not a turn sum).
        # `last_turn_usage` accumulates across the tool-call cycle inside
        # one Agent.run; reset on on_turn_start. `session_usage` runs
        # for the lifetime of the TUI process.
        #
        # Thread safety: these three attrs are rebound (never mutated in
        # place) by the worker thread; the UI thread reads them lock-free.
        # Safe because Usage is frozen=True, so each rebind is an atomic
        # swap under the GIL — a torn read is impossible. Do not add
        # mutable per-turn buffers here without re-evaluating this.
        self.last_call_usage: Usage | None = None
        self.last_turn_usage: Usage = Usage.zero()
        self.session_usage: Usage = Usage.zero()

    def _invalidate(self) -> None:
        self.app.invalidate()

    def on_turn_start(self, prompt: str) -> None:
        self.last_turn_usage = Usage.zero()

    def on_turn_end(self, result: str) -> None:
        pass

    def on_usage(self, usage: Usage) -> None:
        self.last_call_usage = usage
        self.last_turn_usage = self.last_turn_usage + usage
        self.session_usage = self.session_usage + usage
        self._invalidate()

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
