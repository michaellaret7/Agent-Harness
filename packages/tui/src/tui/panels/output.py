"""Scrollable conversation view with background rendering for cell toggles.

Set window.vertical_scroll and pin the cursor to `_scroll_target` because
wrapped windows ignore get_vertical_scroll. Toggle renders share History's
single-worker pool to keep Rich work off the UI thread.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from prompt_toolkit.application import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

from tui.cells import AssistantCell, Cell, ToolCell
from tui.panels.fragments import (
    ASSISTANT_ARROW_CHARS,
    attach_arrow_handler,
    cell_separator,
)

if TYPE_CHECKING:
    from tui.history import History

#     ================================
# --> Output control
#     ================================


class _OutputControl(FormattedTextControl):
    """FormattedTextControl that routes scroll-wheel events to the OutputPanel.

    Reason: The Window's default mouse handler mutates `vertical_scroll`
    directly, but `_scroll_when_linewrapping` recomputes it on every render
    based on cursor position — wheel ticks would be silently undone. Updating
    the panel's scroll target keeps the cursor and scroll in sync.
    """

    def __init__(self, panel: 'OutputPanel', **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._panel = panel

    def mouse_handler(self, mouse_event: MouseEvent) -> Any:
        et = mouse_event.event_type

        if et == MouseEventType.SCROLL_UP:
            self._panel.scroll_lines(-self._panel.WHEEL_STEP)
            return None

        if et == MouseEventType.SCROLL_DOWN:
            self._panel.scroll_lines(self._panel.WHEEL_STEP)
            return None

        return super().mouse_handler(mouse_event)

#     ================================
# --> Output panel
#     ================================


class OutputPanel:
    """Scrollable conversation view with two modes.

    follow_tail anchors at the bottom; otherwise `_scroll_target` fixes the top
    line. Pinning the cursor there keeps Window's auto-scroll aligned.
    """

    PAGE_STEP = 10
    WHEEL_STEP = 3

    def __init__(self, history: 'History') -> None:
        self.history = history
        self.follow_tail = True
        self._scroll_target = 0  # logical line index of topmost visible line
        # Version-keyed cache: rebuild only when history.version advances.
        self._cached_version: int = -1
        self._cached_total_lines: int = 0
        self._cached_cell_count: int = 0
        self._cached_ft: FormattedText = FormattedText()
        # True while the viewport was pinned by a click (vs. a wheel scroll).
        # Auto-clears when new cells arrive so the view re-follows the bottom
        # instead of getting stuck on the old position while a tool keeps
        # adding output below.
        self._click_pinned = False

        self.control = _OutputControl(
            panel=self,
            text=self._get_text,
            # focusable=False so a click anywhere in the output region (text,
            # not just on an arrow handler) doesn't move keyboard focus away
            # from the input prompt — that used to read as "TUI frozen, typing
            # does nothing". Mouse handlers and `get_cursor_position` still
            # fire regardless of focusability.
            focusable=False,
            show_cursor=False,
            get_cursor_position=self._get_cursor_position,
        )

        self.window = Window(
            content=self.control,
            wrap_lines=True,
            always_hide_cursor=True,
            allow_scroll_beyond_bottom=False,
        )

    # ----------------------------------------
    # Render hooks
    # ----------------------------------------

    def _ensure_fresh(self) -> None:
        """Rebuild cached fragments only when history.version changes.

        Concatenate each cell's pre-parsed fragments and attach per-cell tool-toggle
        handlers, avoiding ANSI parsing on the UI thread.
        """
        v = self.history.version

        if v == self._cached_version:
            return

        cells = self.history.snapshot()

        # New cells appended since the last rebuild → re-engage follow_tail if
        # the viewport was only click-pinned. A wheel scroll clears
        # `_click_pinned`, so genuine user scrolling is preserved.
        if self._click_pinned and len(cells) > self._cached_cell_count:
            self.follow_tail = True
            self._click_pinned = False

        fragments: list = []
        prev_cell: Cell | None = None

        for cell in cells:
            # Fragments-truthiness instead of ansi-truthiness: the streaming
            # fast path on AssistantCell sets fragments directly without
            # going through the ANSI roundtrip, so a fast-path-only cell has
            # truthy fragments but an effectively-empty `ansi` sentinel.
            if not cell.fragments:
                continue

            if prev_cell is not None:
                fragments.append(('', cell_separator(prev_cell, cell)))

            cell_frags: list = list(cell.fragments)

            if isinstance(cell, ToolCell):
                tool_handler = self._make_toggle_handler(cell.tool_call_id)
                cell_frags = attach_arrow_handler(cell_frags, tool_handler)

            elif isinstance(cell, AssistantCell) and cell.reasoning and (cell.content or cell.done):
                handler = self._make_reasoning_handler(cell.cell_id)
                cell_frags = attach_arrow_handler(cell_frags, handler, arrows=ASSISTANT_ARROW_CHARS)

            fragments.extend(cell_frags)
            prev_cell = cell

        newline_count = sum(text.count('\n') for _, text, *_ in fragments)

        self._cached_version = v
        self._cached_total_lines = newline_count + 1 if fragments else 0
        self._cached_cell_count = len(cells)
        self._cached_ft = FormattedText(fragments)

    def _make_toggle_handler(self, tool_call_id: str) -> Callable[[MouseEvent], Any]:
        """Build a per-fragment mouse handler that toggles one tool cell.

        Listens to MOUSE_DOWN since several terminals (Windows Terminal, some
        xterm modes) deliver press-only events without the matching MOUSE_UP.
        Freezes the viewport before mutating history so the clicked tool keeps
        its visual position — expanded content pushes the lines below it down
        instead of yanking the whole page upward.
        """
        history = self.history

        def handler(mouse_event: MouseEvent) -> Any:
            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                self._dispatch_toggle(lambda: history.toggle_tool_expand(tool_call_id))
                return None

            return NotImplemented

        return handler

    def _make_reasoning_handler(self, cell_id: str) -> Callable[[MouseEvent], Any]:
        """Per-fragment mouse handler that toggles an AssistantCell's reasoning.

        Same viewport-freeze pattern as `_make_toggle_handler` so expanding the
        thinking block pushes content downward instead of jumping the page.
        """
        history = self.history

        def handler(mouse_event: MouseEvent) -> Any:
            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                self._dispatch_toggle(lambda: history.toggle_assistant_reasoning(cell_id))
                return None

            return NotImplemented

        return handler

    def _dispatch_toggle(self, toggle_fn: Callable[[], None]) -> None:
        """Freeze the viewport, then queue a toggle/render on History's shared worker.

        Freezing, reclamping, and invalidation run on the UI thread. Heavy rendering
        stays in the shared pool so repeated clicks cannot spawn competing workers.
        """
        self._freeze_viewport()

        app = get_app()
        loop = app.loop

        def _worker() -> None:
            try:
                toggle_fn()

            finally:
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(self._reclamp_after_resize)
                    loop.call_soon_threadsafe(app.invalidate)

        self.history.submit_render(_worker)

    def _freeze_viewport(self) -> None:
        """Pin the current top line in `_scroll_target` before mutating history.

        Mark the pin as click-induced so `_ensure_fresh` releases it when new cells
        arrive.
        """
        info = self.window.render_info

        if not info or not info.window_height:
            return

        if self.follow_tail:
            total = self._total_lines()
            self._scroll_target = max(0, total - info.window_height)
            self.follow_tail = False
            self._click_pinned = True

        self.window.vertical_scroll = self._scroll_target

    def _reclamp_after_resize(self) -> None:
        """Re-validate `_scroll_target` after total_lines changes.

        Collapsing a tool can shrink content below the viewport's top — if so
        we'd otherwise be pinned past the new bottom. Snap back to follow_tail
        when that happens so the empty space at the bottom is reclaimed.
        """
        info = self.window.render_info

        if not info or not info.window_height:
            return

        total = self._total_lines()
        topmost = max(0, total - info.window_height)

        if self._scroll_target >= topmost:
            self._scroll_target = topmost
            self.follow_tail = True
            self._click_pinned = False

        self.window.vertical_scroll = self._scroll_target

    def _get_text(self) -> FormattedText:
        self._ensure_fresh()

        return self._cached_ft

    def _total_lines(self) -> int:
        self._ensure_fresh()

        return self._cached_total_lines

    def _get_cursor_position(self) -> Point:
        # Do NOT call _ensure_fresh() here. prompt_toolkit invokes this
        # immediately after _get_text() inside create_content, so the cache
        # is already populated for the fragments it just received. Refreshing
        # again here can race the worker thread bumping history.version, which
        # would return a cursor sized for a newer cache version than the
        # fragments prompt_toolkit is about to index — yielding IndexError
        # inside _scroll_when_linewrapping when y exceeds len(fragment_lines).
        total = self._cached_total_lines

        if self.follow_tail or total == 0:
            return Point(x=0, y=max(0, total - 1))

        return Point(x=0, y=min(self._scroll_target, total - 1))

    # ----------------------------------------
    # Scroll API
    # ----------------------------------------

    def scroll_lines(self, delta: int) -> None:
        """Scroll by `delta` logical lines (negative = up, positive = down)."""
        # User-driven scroll overrides any click-pin: from here on, only
        # `jump_to_bottom` (End key) or scrolling all the way down should
        # re-engage follow_tail.
        self._click_pinned = False

        total = self._total_lines()

        if total == 0:
            return

        info = self.window.render_info
        window_height = info.window_height if info and info.window_height > 0 else 0
        topmost = max(0, total - window_height) if window_height else max(0, total - 1)

        if self.follow_tail:
            if delta >= 0:
                return  # already pinned at bottom

            # Anchor at the line where the top of the viewport currently sits in
            # follow-tail mode. We derive this from `topmost` rather than
            # `info.first_visible_line()` because info can be stale during
            # streaming — if it reflects a past frame when content fit in the
            # window, first_visible_line() returns 0 and the user gets stuck at
            # the top after a single PgUp. `total - window_height` is robust
            # because window_height is stable between resizes.
            self._scroll_target = topmost
            self.follow_tail = False

        self._scroll_target = max(0, self._scroll_target + delta)

        if self._scroll_target >= topmost:
            self._scroll_target = topmost
            self.follow_tail = True
            return

        # Push the scroll position to the Window directly. With wrap_lines=True,
        # `Window._scroll_when_linewrapping` ignores get_vertical_scroll, so a
        # callable wouldn't be honored. Mutating vertical_scroll here is safe
        # because the cursor (pinned at _scroll_target) keeps the auto-adjust
        # algorithm consistent with this value.
        self.window.vertical_scroll = self._scroll_target

    def page_up(self) -> None:
        self.scroll_lines(-self.PAGE_STEP)

    def page_down(self) -> None:
        self.scroll_lines(self.PAGE_STEP)

    def jump_to_bottom(self) -> None:
        self.follow_tail = True
        self._click_pinned = False
        self._scroll_target = max(0, self._total_lines() - 1)
