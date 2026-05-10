"""prompt_toolkit panels: OutputPanel, InputPanel, StatusBar.

OutputPanel renders the joined ANSI of all cells. Scrolling is driven by a
`_scroll_target` line index. We mutate `window.vertical_scroll` directly,
because `Window._scroll_when_linewrapping` ignores `get_vertical_scroll` when
`wrap_lines=True`. The virtual cursor is pinned to the same line so the
Window's auto-scroll-to-cursor logic stays consistent with our requested
position. Status bar pulls live state from a callback. No background colors
are set anywhere — the terminal's native theme shows through.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.widgets import TextArea

if TYPE_CHECKING:
    from tui.history import History

#     ================================
# --> Helper funcs
#     ================================


def _joined_ansi(history: 'History') -> str:
    cells = history.snapshot()

    return '\n\n'.join(cell.ansi for cell in cells if cell.ansi)

#     ================================
# --> Output panel
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


class OutputPanel:
    """Scrollable ANSI view of conversation history.

    Two modes:
      - follow_tail=True: view stays anchored at the bottom; new content
        pushes upward.
      - follow_tail=False: view is fixed at `_scroll_target` (logical line
        index of the topmost visible line).

    The cursor is pinned to `_scroll_target` so that the Window's built-in
    auto-scroll-to-cursor algorithm cooperates rather than fights with our
    requested scroll position.
    """

    PAGE_STEP = 10
    WHEEL_STEP = 3

    def __init__(self, history: 'History') -> None:
        self.history = history
        self.follow_tail = True
        self._scroll_target = 0  # logical line index of topmost visible line
        # Snapshot cache shared by _get_text and _get_cursor_position so they
        # always see the same content. Without this, the worker thread can
        # append a cell between the two calls, leaving the cursor's `y` past
        # `line_count` and triggering a render-time IndexError.
        self._cached_text: str = ''
        self._cached_total_lines: int = 0

        self.control = _OutputControl(
            panel=self,
            text=self._get_text,
            focusable=True,
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

    def _refresh_text(self) -> str:
        full = _joined_ansi(self.history)
        self._cached_text = full
        self._cached_total_lines = full.count('\n') + 1 if full else 0

        return full

    def _get_text(self) -> ANSI:
        return ANSI(self._refresh_text())

    def _total_lines(self) -> int:
        self._refresh_text()

        return self._cached_total_lines

    def _get_cursor_position(self) -> Point:
        total = self._cached_total_lines

        if self.follow_tail or total == 0:
            return Point(x=0, y=max(0, total - 1))

        return Point(x=0, y=min(self._scroll_target, total - 1))

    # ----------------------------------------
    # Scroll API
    # ----------------------------------------

    def scroll_lines(self, delta: int) -> None:
        """Scroll by `delta` logical lines (negative = up, positive = down)."""
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
        self._scroll_target = max(0, self._total_lines() - 1)

#     ================================
# --> Input panel
#     ================================


class InputPanel:
    """Multi-line text entry. Enter submits via the parent App's key binding."""

    def __init__(self) -> None:
        self.history = InMemoryHistory()

        self.area = TextArea(
            height=Dimension(min=1, max=6),
            multiline=True,
            wrap_lines=True,
            history=self.history,
            prompt='> ',
        )

    @property
    def text(self) -> str:
        return self.area.text

    def clear(self) -> None:
        self.area.text = ''

#     ================================
# --> Status bar
#     ================================


class StatusBar:
    """Single-line dim status: provider/model · cwd · iter · keybinds."""

    def __init__(self, get_status: Callable[[], dict]) -> None:
        self.get_status = get_status

        self.control = FormattedTextControl(text=self._get_text)

        self.window = Window(
            content=self.control,
            height=1,
        )

    def _get_text(self) -> FormattedText:
        s = self.get_status()
        provider = s.get('provider', '?')
        model = s.get('model', '?')
        cwd = s.get('cwd', '?')
        running = s.get('running', False)
        scroll_locked = s.get('scroll_locked', False)
        scroll_y = s.get('scroll_y', 0)
        copy_mode = s.get('copy_mode', False)

        left_segments: list[tuple[str, str]] = [
            ('class:status', f' {provider}/{model}  ·  {cwd}'),
        ]

        if running:
            left_segments.append(('class:status.running', '  ·  running…'))

        if scroll_locked:
            left_segments.append(('class:status.locked', f'  ·  [scrolled y={scroll_y}]'))

        if copy_mode:
            left_segments.append(('class:status.copy', '  ·  [COPY MODE — drag to select, Ctrl+G to exit]'))
            right = '  Enter send · PgUp/PgDn scroll · End jump · Ctrl+G exit copy · Ctrl+C exit '
        else:
            right = '  Enter send · PgUp/PgDn or wheel scroll · Ctrl+G copy mode · Esc cancel · Ctrl+C exit '

        return FormattedText(left_segments + [('class:status', '   '), ('class:status', right)])
