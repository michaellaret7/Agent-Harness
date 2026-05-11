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

from prompt_toolkit.application import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.widgets import TextArea

from tui.cells import ToolCell

if TYPE_CHECKING:
    from tui.history import History

#     ================================
# --> Helper funcs
#     ================================

TOOL_ARROW_CHARS = ('⮞', '⮟')


def attach_arrow_handler(
    fragments: list,
    handler: Callable[[MouseEvent], Any],
) -> list:
    """Wrap the click handler around only the leading arrow glyph.

    Scans fragments left-to-right, finds the first ⮞/⮟ character, and splits
    that fragment so the handler is bound to the arrow (plus its trailing
    space, if any) and nothing else. Everything before/after stays inert.
    """
    result: list = []
    attached = False

    for style, text, *_ in fragments:

        if attached:
            result.append((style, text))
            continue

        idx = -1

        for ch in TOOL_ARROW_CHARS:
            i = text.find(ch)

            if i != -1 and (idx == -1 or i < idx):
                idx = i

        if idx == -1:
            result.append((style, text))
            continue

        end = idx + 1

        if end < len(text) and text[end] == ' ':
            end += 1

        before = text[:idx]
        arrow_part = text[idx:end]
        after = text[end:]

        if before:
            result.append((style, before))

        result.append((style, arrow_part, handler))

        if after:
            result.append((style, after))

        attached = True

    return result

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
        # Version-keyed cache: rebuild only when history.version advances.
        self._cached_version: int = -1
        self._cached_total_lines: int = 0
        self._cached_ft: FormattedText = FormattedText()

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

    def _ensure_fresh(self) -> None:
        """Rebuild the cache iff history.version advanced. No-op otherwise.

        ToolCell fragments are tagged with a per-cell mouse handler so the user
        can click any tool block to toggle just its expand state. Other cells
        pass through as plain (style, text) fragments.
        """
        v = self.history.version

        if v == self._cached_version:
            return

        cells = self.history.snapshot()
        fragments: list = []

        for cell in cells:
            if not cell.ansi:
                continue

            if fragments:
                fragments.append(('', '\n\n'))

            cell_frags = to_formatted_text(ANSI(cell.ansi))

            if isinstance(cell, ToolCell):
                handler = self._make_toggle_handler(cell.tool_call_id)
                cell_frags = attach_arrow_handler(list(cell_frags), handler)

            fragments.extend(cell_frags)

        plain = ''.join(text for _, text, *_ in fragments)

        self._cached_version = v
        self._cached_total_lines = plain.count('\n') + 1 if plain else 0
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
                self._freeze_viewport()
                history.toggle_tool_expand(tool_call_id)
                self._reclamp_after_resize()
                get_app().invalidate()
                return None

            return NotImplemented

        return handler

    def _freeze_viewport(self) -> None:
        """Pin the viewport at its current top before a history mutation.

        In follow_tail mode the topmost visible line is implicit (total -
        window_height). We snap that to an explicit `_scroll_target` so the
        next render doesn't auto-anchor to the new bottom.
        """
        info = self.window.render_info

        if not info or not info.window_height:
            return

        if self.follow_tail:
            total = self._total_lines()
            self._scroll_target = max(0, total - info.window_height)
            self.follow_tail = False

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

        self.window.vertical_scroll = self._scroll_target

    def _get_text(self) -> FormattedText:
        self._ensure_fresh()

        return self._cached_ft

    def _total_lines(self) -> int:
        self._ensure_fresh()

        return self._cached_total_lines

    def _get_cursor_position(self) -> Point:
        self._ensure_fresh()
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
            right = '  Enter send · PgUp/PgDn or wheel scroll · click tool to expand · Ctrl+G copy mode · Esc cancel · Ctrl+C exit '

        return FormattedText(left_segments + [('class:status', '   '), ('class:status', right)])
