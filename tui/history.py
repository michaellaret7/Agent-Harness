"""Conversation history container.

Single source of truth for what the UI renders. Mutated by Sink (worker
thread); read by the renderer (UI thread). A monotonic `version` counter
keys the OutputPanel's render cache — bumped once per visible mutation.
Streaming cell renders are throttled (STREAM_RENDER_INTERVAL_S) to keep
Rich Markdown off the hot path during token-by-token deltas.
"""
from __future__ import annotations

import threading
import time

from tui.cells import AssistantCell, Cell, ErrorCell, HeaderCell, ToolCell, UserCell

# Cap intermediate streaming re-renders to ~25fps. Final render on
# end_assistant/end_tool always fires regardless.
STREAM_RENDER_INTERVAL_S = 0.04


class History:
    def __init__(self) -> None:
        self._cells: list[Cell] = []
        self._tool_index: dict[str, ToolCell] = {}
        self._lock = threading.Lock()
        self.width = 80  # updated by UI thread on resize
        # Bumped whenever a frame should be re-rendered. UI uses this as a
        # cache key — equal version means the FormattedText can be reused.
        self.version = 0

    def snapshot(self) -> list[Cell]:
        """Lock-protected read of the cell list (shallow copy)."""
        with self._lock:
            return list(self._cells)

    def _bump_version(self) -> None:
        """Atomic version increment.

        `self.version += 1` is NOT thread-safe in CPython — the load/add/store
        sequence can interleave between the worker thread and the UI thread's
        click handlers, silently losing a bump and leaving the OutputPanel's
        version-keyed cache stale. Lock-protected here so concurrent toggle
        clicks and tool-result writes can't drop a frame.
        """
        with self._lock:
            self.version += 1

    def append_header(
        self,
        provider: str,
        model: str,
        cwd: str,
        tools: tuple[str, ...] = (),
    ) -> None:
        cell = HeaderCell(provider=provider, model=model, cwd=cwd, tools=tools)
        cell.render(self.width)

        with self._lock:
            self._cells.append(cell)

        self._bump_version()

    def append_user(self, text: str) -> None:
        cell = UserCell(text=text)
        cell.render(self.width)

        with self._lock:
            self._cells.append(cell)

        self._bump_version()

    def start_assistant(self) -> AssistantCell:
        """Append a fresh AssistantCell. Returns it for streaming mutation."""
        cell = AssistantCell()

        with self._lock:
            self._cells.append(cell)

        self._bump_version()

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

        now = time.monotonic()

        if now - cell._last_render_t >= STREAM_RENDER_INTERVAL_S:
            cell.render(self.width)
            cell._last_render_t = now
            self._bump_version()

    def append_content(self, text: str) -> None:
        cell = self.last_assistant()
        cell.content += text

        now = time.monotonic()

        if now - cell._last_render_t >= STREAM_RENDER_INTERVAL_S:
            cell.render(self.width)
            cell._last_render_t = now
            self._bump_version()

    def end_assistant(self) -> None:
        """Mark the last AssistantCell done. Drop if empty.

        `cell.render()` runs outside the lock — Rich Markdown rendering can
        take dozens of milliseconds on long replies, and holding `_lock`
        across it blocks UI-thread click handlers (which call `snapshot()`)
        on every keystroke.
        """
        target: AssistantCell | None = None

        with self._lock:
            if not self._cells:
                return

            last = self._cells[-1]

            if not isinstance(last, AssistantCell):
                return

            if last.is_empty():
                self._cells.pop()
                self.version += 1
                return

            last.done = True
            target = last

        target.render(self.width)
        self._bump_version()

    def mark_assistant_interrupted(self) -> None:
        target: AssistantCell | None = None

        with self._lock:
            for cell in reversed(self._cells):
                if isinstance(cell, AssistantCell):
                    cell.done = True
                    cell.interrupted = True
                    target = cell
                    break

        if target is None:
            return

        target.render(self.width)
        self._bump_version()

    def append_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        cell = ToolCell(
            name=name,
            args_json=args_json,
            tool_call_id=tool_call_id,
            started_at=time.monotonic(),
        )
        cell.render(self.width)

        with self._lock:
            self._cells.append(cell)
            self._tool_index[tool_call_id] = cell

        self._bump_version()

    def update_tool_result(self, tool_call_id: str, result: str) -> None:
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        cell.result = result
        cell.status = 'error' if result.startswith('error:') else 'ok'
        cell.ended_at = time.monotonic()

        # Auto-expand failures so the user immediately sees what broke.
        if cell.status == 'error':
            cell.expanded = True

        cell.render(self.width)
        self._bump_version()

    def mark_tool_interrupted(self, tool_call_id: str) -> None:
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        cell.result = '[interrupted]'
        cell.status = 'error'
        cell.ended_at = time.monotonic()
        cell.expanded = True
        cell.render(self.width)
        self._bump_version()

    def toggle_tool_expand(self, tool_call_id: str) -> None:
        """Flip the expand state of one tool cell, identified by call id."""
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        cell.expanded = not cell.expanded
        cell.render(self.width)
        self._bump_version()

    def append_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None:
        """Attach a file diff to its originating ToolCell (no new cell created)."""
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        cell.diff_path = path
        cell.diff_before = before
        cell.diff_after = after
        cell.render(self.width)
        self._bump_version()

    def toggle_tool_diff_expand(self, tool_call_id: str) -> None:
        """Flip the diff-expand state of one ToolCell."""
        cell = self._tool_index.get(tool_call_id)

        if cell is None or not cell.has_diff():
            return

        cell.diff_expanded = not cell.diff_expanded
        cell.render(self.width)
        self._bump_version()

    def toggle_assistant_reasoning(self, cell_id: str) -> None:
        """Flip the reasoning-collapse state of one AssistantCell."""
        with self._lock:
            target: AssistantCell | None = None

            for cell in self._cells:
                if isinstance(cell, AssistantCell) and cell.cell_id == cell_id:
                    target = cell
                    break

        if target is None:
            return

        target.reasoning_expanded = not target.reasoning_expanded
        target.render(self.width)
        self._bump_version()

    def append_error(self, message: str) -> None:
        cell = ErrorCell(message=message)
        cell.render(self.width)

        with self._lock:
            self._cells.append(cell)

        self._bump_version()

    def rerender_all(self) -> None:
        """Re-render every cell at the current width (called on resize)."""
        with self._lock:
            cells = list(self._cells)

        for cell in cells:
            cell.render(self.width)

        self._bump_version()
