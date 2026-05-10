"""Conversation history container.

Single source of truth for what the UI renders. Mutated by Sink (worker
thread); read by the renderer (UI thread). Uses a single threading.Lock —
mutations and the render walk both take it. Render walk is fast (string
join), so contention is fine.
"""
from __future__ import annotations

import threading

from tui.cells import AssistantCell, Cell, ErrorCell, ToolCell, UserCell


class History:
    def __init__(self) -> None:
        self._cells: list[Cell] = []
        self._tool_index: dict[str, ToolCell] = {}
        self._lock = threading.Lock()
        self.width = 80  # updated by UI thread on resize

    def snapshot(self) -> list[Cell]:
        """Lock-protected read of the cell list (shallow copy)."""
        with self._lock:
            return list(self._cells)

    def append_user(self, text: str) -> None:
        cell = UserCell(text=text)
        cell.render(self.width)

        with self._lock:
            self._cells.append(cell)

    def start_assistant(self) -> AssistantCell:
        """Append a fresh AssistantCell. Returns it for streaming mutation."""
        cell = AssistantCell()

        with self._lock:
            self._cells.append(cell)

        return cell

    def last_assistant(self) -> AssistantCell:
        """Return the trailing AssistantCell only if it's the last cell and
        still streaming. Otherwise start a fresh one — done assistant cells
        belong to a finished turn and must not be re-extended.
        """
        with self._lock:
            if self._cells:
                last = self._cells[-1]

                if isinstance(last, AssistantCell) and not last.done:
                    return last

        return self.start_assistant()

    def append_reasoning(self, text: str) -> None:
        cell = self.last_assistant()
        cell.reasoning += text
        cell.render(self.width)

    def append_content(self, text: str) -> None:
        cell = self.last_assistant()
        cell.content += text
        cell.render(self.width)

    def end_assistant(self) -> None:
        """Mark the last AssistantCell done. Drop if empty."""
        with self._lock:
            if not self._cells:
                return

            last = self._cells[-1]

            if not isinstance(last, AssistantCell):
                return

            if last.is_empty():
                self._cells.pop()
                return

            last.done = True
            last.render(self.width)

    def mark_assistant_interrupted(self) -> None:
        with self._lock:
            for cell in reversed(self._cells):
                if isinstance(cell, AssistantCell):
                    cell.done = True
                    cell.interrupted = True
                    cell.render(self.width)
                    return

    def append_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        cell = ToolCell(name=name, args_json=args_json, tool_call_id=tool_call_id)
        cell.render(self.width)

        with self._lock:
            self._cells.append(cell)
            self._tool_index[tool_call_id] = cell

    def update_tool_result(self, tool_call_id: str, result: str) -> None:
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        cell.result = result
        cell.status = 'error' if result.startswith('error:') else 'ok'
        cell.render(self.width)

    def mark_tool_interrupted(self, tool_call_id: str) -> None:
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        cell.result = '[interrupted]'
        cell.status = 'error'
        cell.render(self.width)

    def append_error(self, message: str) -> None:
        cell = ErrorCell(message=message)
        cell.render(self.width)

        with self._lock:
            self._cells.append(cell)

    def rerender_all(self) -> None:
        """Re-render every cell at the current width (called on resize)."""
        with self._lock:
            cells = list(self._cells)

        for cell in cells:
            cell.render(self.width)
