"""Conversation history container.

Single source of truth for what the UI renders. Mutated by Sink (worker
thread); read by the renderer (UI thread). A monotonic `version` counter
keys the OutputPanel's render cache — bumped once per visible mutation.
Streaming cell renders are throttled (STREAM_RENDER_INTERVAL_S) to keep
even the fast plain-text fragment build off the hot path during
token-by-token deltas.

Heavy renders (final Markdown after end_assistant, toggle re-renders on
clicks) are submitted to `_render_pool` — a single-worker executor — so they
never pile up and contend for the GIL. Serializing them onto one worker is
the right trade: each individual render reads/writes one cell under its own
render_lock, and the UI never blocks on background renders since it just
reads cached `cell.fragments`.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from tui.cells import AssistantCell, Cell, ErrorCell, HeaderCell, ToolCell, UserCell

# Throttle the fast-path streaming render. Even the plain-text fragment build
# isn't free — at 12 Hz with growing content it still walks O(n) text per
# delta, and each invalidate schedules a redraw the UI loop must service.
# 8 Hz is below the human flicker-fusion threshold for text and gives the
# event loop generous breathing room between bumps.
STREAM_RENDER_INTERVAL_S = 1 / 8


class History:
    def __init__(self) -> None:
        self._cells: list[Cell] = []
        self._tool_index: dict[str, ToolCell] = {}
        self._lock = threading.Lock()
        self.width = 80  # updated by UI thread on resize
        # Bumped whenever a frame should be re-rendered. UI uses this as a
        # cache key — equal version means the FormattedText can be reused.
        self.version = 0
        # Single-worker pool for heavy renders (final Markdown, toggle
        # re-renders, resize re-renders). Serializing onto one worker keeps
        # Rich from spinning up multiple concurrent renders that all fight
        # for the GIL — which is exactly what made the UI feel locked up
        # during streaming. `max_workers=1` is intentional.
        self._render_pool = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix='history-render',
        )
        # Cross-thread Rich-render serializer. Pre-parallel-tools, the agent
        # had exactly one worker thread driving sink events, so cell.render()
        # calls were implicitly serialized. Parallel tool dispatch broke that:
        # N worker threads now hit `append_tool_start` / `update_tool_result`
        # at the same time, each running Rich on the calling thread. Concurrent
        # Rich renders fight for the GIL and starve the asyncio loop — the
        # observable symptom is a UI freeze during parallel tool batches. This
        # lock collapses every cell render in this module back to one-at-a-time
        # so the main-thread event loop always has a free GIL window between
        # render bursts. Held only across the render call itself, never across
        # `self._lock`, so reader snapshots are never blocked.
        self._render_serializer = threading.Lock()

    def submit_render(self, fn: Callable[[], None]) -> Future:
        """Schedule a render callable on the single background worker.

        Callers should already hold or take the relevant `cell.render_lock`
        inside `fn` so concurrent renders on the same cell stay serialized
        with foreground writes from this History.
        """
        return self._render_pool.submit(fn)

    def _render(self, cell: Cell, width: int | None = None) -> None:
        """Render a cell under the global render serializer.

        Every Rich render in this module funnels through here so that the
        N worker threads spawned by parallel tool dispatch can never run
        Rich concurrently — see `_render_serializer` in `__init__` for why.
        `width` defaults to `self.width` but accepts an explicit value for
        callbacks that capture width at submission time.
        """
        target_width = self.width if width is None else width

        with self._render_serializer:
            cell.render(target_width)

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
        self._render(cell)

        with self._lock:
            self._cells.append(cell)

        self._bump_version()

    def append_user(self, text: str) -> None:
        cell = UserCell(text=text)
        self._render(cell)

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

        with cell.render_lock:
            cell.reasoning += text

            now = time.monotonic()

            if now - cell._last_render_t >= STREAM_RENDER_INTERVAL_S:
                self._render(cell)
                cell._last_render_t = now
                bump = True
            else:
                bump = False

        if bump:
            self._bump_version()

    def append_content(self, text: str) -> None:
        cell = self.last_assistant()

        with cell.render_lock:
            cell.content += text

            now = time.monotonic()

            if now - cell._last_render_t >= STREAM_RENDER_INTERVAL_S:
                self._render(cell)
                cell._last_render_t = now
                bump = True
            else:
                bump = False

        if bump:
            self._bump_version()

    def end_assistant(self) -> None:
        """Mark the last AssistantCell done. Drop if empty.

        Splits into two phases:
          1. Immediate, on the worker thread: flip `done=True`, run the fast
             plain-text render, bump version. The user sees the final
             content right away, with the streaming cursor removed. This
             frees the worker to move on to the next tool call.
          2. Background, on the render pool: run the full Rich Markdown
             render. Once it lands, flip `markdown_ready=True` and bump
             version again so the UI swaps in the styled output.

        The previous synchronous Markdown render here is the single biggest
        worker-thread GIL hog at end-of-message — for multi-KB replies it
        could hold the GIL for hundreds of ms, starving every UI event in
        flight (scroll, click) for that window. Deferral fixes that without
        regressing visual fidelity.
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

        with target.render_lock:
            self._render(target)

        self._bump_version()

        width = self.width

        def _markdown_render() -> None:
            with target.render_lock:
                target.markdown_ready = True
                self._render(target, width)

            self._bump_version()

        self._render_pool.submit(_markdown_render)

    def mark_assistant_interrupted(self) -> None:
        target: AssistantCell | None = None

        with self._lock:
            for cell in reversed(self._cells):
                if isinstance(cell, AssistantCell):
                    target = cell
                    break

        if target is None:
            return

        with target.render_lock:
            target.done = True
            target.interrupted = True
            self._render(target)

        self._bump_version()

    def append_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        cell = ToolCell(
            name=name,
            args_json=args_json,
            tool_call_id=tool_call_id,
            started_at=time.monotonic(),
        )
        self._render(cell)

        with self._lock:
            self._cells.append(cell)
            self._tool_index[tool_call_id] = cell

        self._bump_version()

    def update_tool_result(self, tool_call_id: str, result: str) -> None:
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        with cell.render_lock:
            cell.result = result
            cell.status = 'error' if result.startswith('error:') else 'ok'
            cell.ended_at = time.monotonic()

            # Reason: do NOT auto-expand errors. The status tail already shows
            # the first 40 chars of the error in red — enough to read what
            # broke. Expanding every failure turned bursts of failed tool
            # calls into a wall of red blocks, multiplying Rich render work
            # on the worker and FormattedText rebuilds on the UI thread.
            # Users can click ⮞ to expand any specific cell.
            self._render(cell)

        self._bump_version()

    def mark_tool_interrupted(self, tool_call_id: str) -> None:
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        with cell.render_lock:
            cell.result = '[interrupted]'
            cell.status = 'error'
            cell.ended_at = time.monotonic()
            cell.expanded = True
            self._render(cell)

        self._bump_version()

    def toggle_tool_expand(self, tool_call_id: str) -> None:
        """Flip the expand state of one tool cell, identified by call id."""
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        with cell.render_lock:
            cell.expanded = not cell.expanded
            self._render(cell)

        self._bump_version()

    def append_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None:
        """Attach a file diff to its originating ToolCell (no new cell created)."""
        cell = self._tool_index.get(tool_call_id)

        if cell is None:
            return

        with cell.render_lock:
            cell.diff_path = path
            cell.diff_before = before
            cell.diff_after = after
            self._render(cell)

        self._bump_version()

    def toggle_tool_diff_expand(self, tool_call_id: str) -> None:
        """Flip the diff-expand state of one ToolCell."""
        cell = self._tool_index.get(tool_call_id)

        if cell is None or not cell.has_diff():
            return

        with cell.render_lock:
            cell.diff_expanded = not cell.diff_expanded
            self._render(cell)

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

        with target.render_lock:
            target.reasoning_expanded = not target.reasoning_expanded
            self._render(target)

        self._bump_version()

    def append_error(self, message: str) -> None:
        cell = ErrorCell(message=message)
        self._render(cell)

        with self._lock:
            self._cells.append(cell)

        self._bump_version()

    def rerender_all(self) -> None:
        """Re-render every cell at the current width (called on resize).

        Each cell's `render_lock` serializes with concurrent worker-thread
        mutations on the same cell — important here because this method runs
        from a worker thread (off the UI loop) while streaming may still be
        going on.
        """
        with self._lock:
            cells = list(self._cells)

        for cell in cells:
            with cell.render_lock:
                self._render(cell)

        self._bump_version()
