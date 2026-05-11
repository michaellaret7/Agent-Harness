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

from functools import partial
from typing import TYPE_CHECKING, Any, Callable

from prompt_toolkit.application import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout.containers import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.widgets import TextArea

from tui.cells import AssistantCell, Cell, ErrorCell, HeaderCell, ToolCell, UserCell

if TYPE_CHECKING:
    from tui.history import History

#     ================================
# --> Helper funcs
#     ================================

TOOL_ARROW_CHARS = ('⮞', '⮟')
ASSISTANT_ARROW_CHARS = ('▸', '▾')

# Cells that mark a turn boundary — anything that introduces or interrupts an
# agent turn. Adjacent cells in this set get a full blank line of breathing
# room; everything else (assistant↔tool within a turn) stacks tight.
TURN_BOUNDARY_TYPES = (UserCell, HeaderCell, ErrorCell)


def cell_separator(prev: Cell, curr: Cell) -> str:
    """Vertical breathing room between two adjacent cells."""
    if isinstance(prev, TURN_BOUNDARY_TYPES) or isinstance(curr, TURN_BOUNDARY_TYPES):
        return '\n\n'

    return '\n'


def attach_arrow_handler(
    fragments: list,
    handler: Callable[[MouseEvent], Any],
    arrows: tuple[str, ...] = TOOL_ARROW_CHARS,
) -> list:
    """Wrap the click handler around only the leading arrow glyph.

    Scans fragments left-to-right, finds the first arrow character, and splits
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

        for ch in arrows:
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

        Each cell publishes its own pre-parsed fragments (built once on the
        worker thread inside cell.render); we just concatenate them here. That
        keeps the UI thread off the ANSI parser even when a single streaming
        cell bumps the version 25 times per second.

        ToolCell fragments are tagged with a per-cell mouse handler so the user
        can click any tool block to toggle just its expand state.
        """
        v = self.history.version

        if v == self._cached_version:
            return

        cells = self.history.snapshot()
        fragments: list = []
        prev_cell: Cell | None = None

        for cell in cells:
            if not cell.ansi:
                continue

            if prev_cell is not None:
                fragments.append(('', cell_separator(prev_cell, cell)))

            cell_frags: list = list(cell.fragments)

            if isinstance(cell, ToolCell):
                handler = self._make_toggle_handler(cell.tool_call_id)
                cell_frags = attach_arrow_handler(cell_frags, handler)

            elif isinstance(cell, AssistantCell) and cell.reasoning and (cell.content or cell.done):
                handler = self._make_reasoning_handler(cell.cell_id)
                cell_frags = attach_arrow_handler(cell_frags, handler, arrows=ASSISTANT_ARROW_CHARS)

            fragments.extend(cell_frags)
            prev_cell = cell

        newline_count = sum(text.count('\n') for _, text, *_ in fragments)

        self._cached_version = v
        self._cached_total_lines = newline_count + 1 if fragments else 0
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

    def _make_reasoning_handler(self, cell_id: str) -> Callable[[MouseEvent], Any]:
        """Per-fragment mouse handler that toggles an AssistantCell's reasoning.

        Same viewport-freeze pattern as `_make_toggle_handler` so expanding the
        thinking block pushes content downward instead of jumping the page.
        """
        history = self.history

        def handler(mouse_event: MouseEvent) -> Any:
            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                self._freeze_viewport()
                history.toggle_assistant_reasoning(cell_id)
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


class PlaceholderProcessor(Processor):
    """Append dim placeholder text after the prompt when the buffer is empty.

    Applies only to line 0 — the prompt's line. Once the buffer has any text
    the fragments pass through unchanged, so the placeholder disappears as
    the user types.
    """

    def __init__(self, text: str, style: str = '') -> None:
        self.text = text
        self.style = style

    def apply_transformation(self, transformation_input: TransformationInput) -> Transformation:
        ti = transformation_input

        if ti.lineno != 0 or ti.document.text:
            return Transformation(ti.fragments)

        return Transformation(list(ti.fragments) + [(self.style, self.text)])


class InputPanel:
    """Multi-line text entry with a rounded frame and vertical breathing room.

    The frame is hand-rolled from Window primitives because prompt_toolkit's
    stock `Frame` hardcodes square corners. A 1-row blank pad above and below
    the TextArea centers the prompt vertically. The input grows from 1 to
    MAX_LINES rows as the user types past the visible content; longer input
    scrolls within the area.
    """

    MIN_LINES = 1
    MAX_LINES = 4
    PLACEHOLDER = 'Ask Atlas anything…'
    PROMPT_STR = '   > '

    def __init__(self) -> None:
        self.history = InMemoryHistory()

        # dont_extend_height pins the body's max to its content height.
        # Reason: without it, the parent HSplit absorbs spare rows into the
        # body (max=4), pushing the cursor to the top of an oversized box and
        # leaving the bottom padding visually larger than the top.
        self.area = TextArea(
            height=Dimension(min=self.MIN_LINES, max=self.MAX_LINES),
            multiline=True,
            wrap_lines=True,
            history=self.history,
            prompt=self.PROMPT_STR,
            dont_extend_height=True,
            get_line_prefix=self._continuation_prefix,
            input_processors=[
                PlaceholderProcessor(self.PLACEHOLDER, style='class:input.placeholder'),
            ],
        )

        self.container = self._build_rounded_frame(self.area)

    @classmethod
    def _continuation_prefix(cls, lineno: int, wrap_count: int) -> str:
        """Indent wrap continuations and subsequent logical lines.

        prompt_toolkit renders the `prompt` only on line 0/wrap 0. Every other
        visual row needs leading spaces so wrapped text and Shift+Enter newlines
        hang under the cursor instead of resetting to the left border.
        """
        if lineno == 0 and wrap_count == 0:
            return ''

        return ' ' * len(cls.PROMPT_STR)

    @staticmethod
    def _build_rounded_frame(body: Any) -> HSplit:
        fill = partial(Window, style='class:frame.border')

        top = VSplit([
            fill(width=1, height=1, char='╭'),
            fill(char='─', height=1),
            fill(width=1, height=1, char='╮'),
        ], height=1)

        padded_body = HSplit([
            Window(height=1),
            body,
            Window(height=1),
        ])

        middle = VSplit([
            fill(width=1, char='│'),
            padded_body,
            fill(width=1, char='│'),
        ])

        bottom = VSplit([
            fill(width=1, height=1, char='╰'),
            fill(char='─', height=1),
            fill(width=1, height=1, char='╯'),
        ], height=1)

        return HSplit([top, middle, bottom])

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
            left_segments.append(('class:status.copy', '  ·  [COPY MODE — drag to select, Ctrl+T to exit]'))
            right = '  Enter send · PgUp/PgDn scroll · End jump · Ctrl+T exit copy · Ctrl+C exit '
        else:
            right = '  Enter send · PgUp/PgDn or wheel scroll · click tool to expand · Ctrl+T copy mode · Esc cancel · Ctrl+C exit '

        return FormattedText(left_segments + [('class:status', '   '), ('class:status', right)])
