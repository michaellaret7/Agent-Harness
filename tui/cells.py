"""Conversation cells: pure data + Rich-based render to ANSI.

Each cell is a dataclass holding raw state. `render(width)` produces an ANSI
string, stores it on `self.ansi`, AND pre-parses it into prompt_toolkit
fragments cached on `self.fragments`. Doing the parse here (worker thread)
keeps the UI thread's scroll path off the ANSI parser; the OutputPanel just
concatenates pre-parsed fragments per frame.

Background color is never set — terminal inherits its own theme.
"""
from __future__ import annotations

import difflib
import io
import json
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

#     ================================
# --> Helper funcs
#     ================================

ToolStatus = Literal['running', 'ok', 'error']

ARG_VALUE_TRUNCATE = 60
ARGS_LINE_TRUNCATE = 120
RESULT_INDENT = '     '

STATUS_COLOR: dict[ToolStatus, str] = {
    'running': 'yellow',
    'ok': 'green',
    'error': 'red',
}

STATUS_GLYPH: dict[ToolStatus, str] = {
    'running': '⟳',
    'ok': '✓',
    'error': '✗',
}

# Keys checked first when picking the header's primary-arg display.
ARG_PRIORITY_KEYS = (
    'command', 'cmd',
    'path', 'file', 'file_path', 'filename',
    'pattern', 'query', 'q',
    'url',
)


def render_to_ansi(renderable: RenderableType, width: int) -> str:
    """Render any Rich renderable to a raw ANSI string at the given width."""
    console = Console(
        force_terminal=True,
        color_system='truecolor',
        width=max(width, 20),
        legacy_windows=False,
        file=io.StringIO(),
    )

    with console.capture() as capture:
        console.print(renderable, end='')

    return capture.get()


def parse_ansi_to_fragments(ansi: str) -> list[tuple[str, str]]:
    """Parse ANSI escapes into prompt_toolkit fragments, merging same-style runs.

    Rich flips style on every escape boundary, producing 3-10x more fragments
    than necessary. Coalescing identical-style runs here shrinks the fragment
    count, which directly cuts the per-frame cost of split_lines and the
    FormattedTextControl cache-key tuple inside prompt_toolkit's render path.
    """
    if not ansi:
        return []

    raw = to_formatted_text(ANSI(ansi))

    if not raw:
        return []

    out: list[tuple[str, str]] = []
    cur_style = raw[0][0]
    cur_parts: list[str] = [raw[0][1]]

    for style, text, *_ in raw[1:]:
        if style == cur_style:
            cur_parts.append(text)
            continue

        out.append((cur_style, ''.join(cur_parts)))
        cur_style = style
        cur_parts = [text]

    out.append((cur_style, ''.join(cur_parts)))

    return out


def truncate_oneline(text: str, limit: int) -> str:
    """Collapse to one line and truncate."""
    flat = text.replace('\n', ' ').strip()

    if len(flat) <= limit:
        return flat

    return flat[:limit] + '…'


def extract_primary_arg(args_json: str) -> str:
    """Pick the most informative scalar value from args for the header line."""
    try:
        data = json.loads(args_json) if args_json else {}

    except (ValueError, TypeError):
        return ''

    if not isinstance(data, dict) or not data:
        return ''

    for key in ARG_PRIORITY_KEYS:
        value = data.get(key)

        if isinstance(value, (str, int, float, bool)):
            return truncate_oneline(str(value), ARG_VALUE_TRUNCATE)

    for value in data.values():
        if isinstance(value, (str, int, float, bool)):
            return truncate_oneline(str(value), ARG_VALUE_TRUNCATE)

    return ''


TITLE_COLOR = '#15b49e'

TITLE_ROWS = 6
LETTER_GAP = ''
WORD_GAP = '   '

# ANSI Shadow figlet font. Glyphs are placed adjacent — their own internal
# padding provides the visual gap, so LETTER_GAP is empty.
TITLE_LETTERS: dict[str, tuple[str, ...]] = {
    'A': (
        ' █████╗ ',
        '██╔══██╗',
        '███████║',
        '██╔══██║',
        '██║  ██║',
        '╚═╝  ╚═╝',
    ),
    'T': (
        '████████╗',
        '╚══██╔══╝',
        '   ██║   ',
        '   ██║   ',
        '   ██║   ',
        '   ╚═╝   ',
    ),
    'L': (
        '██╗     ',
        '██║     ',
        '██║     ',
        '██║     ',
        '███████╗',
        '╚══════╝',
    ),
    'S': (
        '███████╗',
        '██╔════╝',
        '███████╗',
        '╚════██║',
        '███████║',
        '╚══════╝',
    ),
}


def render_title(text: str) -> str:
    """Compose a block-letter banner from TITLE_LETTERS, row by row."""
    rows = [''] * TITLE_ROWS

    chars = text.upper()

    for i, ch in enumerate(chars):
        if ch == ' ':
            for r in range(TITLE_ROWS):
                rows[r] += WORD_GAP
            continue

        glyph = TITLE_LETTERS[ch]

        for r in range(TITLE_ROWS):
            rows[r] += glyph[r]

        if i + 1 < len(chars) and chars[i + 1] != ' ':
            for r in range(TITLE_ROWS):
                rows[r] += LETTER_GAP

    return '\n'.join(rows)


def format_duration(seconds: float) -> str:
    """Human-friendly duration: '120ms', '3.4s', '1m12s'."""
    if seconds < 1:
        return f'{seconds * 1000:.0f}ms'

    if seconds < 60:
        return f'{seconds:.1f}s'

    minutes, secs = divmod(int(seconds), 60)

    return f'{minutes}m{secs}s'


# Tinted diff backgrounds. Narrow exception to the "no background" rule —
# only applied to diff body rows so changed lines read like Cursor's inline
# diff. Dark enough to coexist with most terminal themes.
#
# Each color comes in two variants: the full tint (used when no inline
# spans were computed for the line) and a dim/highlight pair (used when
# char-level spans split the line into "context" and "actually changed"
# regions, à la GitHub's word-diff).
DIFF_DEL_STYLE = 'bold #ff8b8b on #3a1d22'
DIFF_DEL_DIM_STYLE = '#c9888a on #2c1619'
DIFF_DEL_HI_STYLE = 'bold #ffd6d6 on #6e2228'

DIFF_INS_STYLE = 'bold #9fdfb1 on #1a2e1f'
DIFF_INS_DIM_STYLE = '#83b696 on #142318'
DIFF_INS_HI_STYLE = 'bold #d4ffd9 on #2a5630'

DIFF_EQ_STYLE = 'dim'
DIFF_SKIP_STYLE = 'dim italic'

# Left indent of the diff panel under a ToolCell. Aligns with the same
# visual offset as the args row.
DIFF_INDENT = 5

# Number of unchanged lines kept on each side of every change region.
# Anything beyond this collapses into a single "⋯ N unchanged ⋯" row.
DIFF_CONTEXT = 3

# Minimum SequenceMatcher ratio for two lines to be treated as a tweak
# (eligible for char-level inline highlighting) rather than a full rewrite.
# Below this they fall back to whole-line tint.
DIFF_INLINE_RATIO = 0.5


@dataclass
class DiffRow:
    """One row of the rendered diff.

    a_lineno / b_lineno are 1-based line numbers in the before/after files,
    or None when the row doesn't exist on that side. For 'skip' rows both
    are None and `line` holds the placeholder text. `spans` carries the
    char ranges (within `line`) that actually changed — when set, those
    chars render with a brighter highlight while the rest stays dim.
    """

    tag: Literal['eq', 'del', 'ins', 'skip']
    a_lineno: int | None
    b_lineno: int | None
    line: str
    spans: list[tuple[int, int]] | None = None


def _inline_spans(
    a_line: str,
    b_line: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]] | None:
    """Char-level changed spans for a paired (del, ins) line.

    Returns (del_spans, ins_spans) when the two lines are similar enough to
    treat as a tweak, else None — meaning the caller should fall back to
    the whole-line tint instead of pretending it's a small edit.
    """
    sm = difflib.SequenceMatcher(a=a_line, b=b_line, autojunk=False)

    if sm.ratio() < DIFF_INLINE_RATIO:
        return None

    del_spans: list[tuple[int, int]] = []
    ins_spans: list[tuple[int, int]] = []

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ('delete', 'replace') and i2 > i1:
            del_spans.append((i1, i2))

        if op in ('insert', 'replace') and j2 > j1:
            ins_spans.append((j1, j2))

    return del_spans, ins_spans


def compute_diff_rows(before: str, after: str) -> tuple[list[DiffRow], int, int]:
    """Build the row list for a before/after pair, tagging each row with
    line numbers from both files and (for paired replaces) char-level spans.
    """
    a = before.splitlines()
    b = after.splitlines()

    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    rows: list[DiffRow] = []
    adds = 0
    dels = 0

    for op, i1, i2, j1, j2 in sm.get_opcodes():

        if op == 'equal':
            for k in range(i2 - i1):
                rows.append(DiffRow('eq', i1 + k + 1, j1 + k + 1, a[i1 + k]))
            continue

        if op == 'delete':
            for k in range(i2 - i1):
                rows.append(DiffRow('del', i1 + k + 1, None, a[i1 + k]))
                dels += 1
            continue

        if op == 'insert':
            for k in range(j2 - j1):
                rows.append(DiffRow('ins', None, j1 + k + 1, b[j1 + k]))
                adds += 1
            continue

        # 'replace': pair del[k] with ins[k] positionally so the eye tracks
        # red→green per slot. For the matched pairs, try a char-level diff.
        del_lines = a[i1:i2]
        ins_lines = b[j1:j2]
        paired = min(len(del_lines), len(ins_lines))

        for k in range(paired):
            spans = _inline_spans(del_lines[k], ins_lines[k])
            del_spans, ins_spans = (spans if spans else (None, None))

            rows.append(DiffRow('del', i1 + k + 1, None, del_lines[k], del_spans))
            dels += 1
            rows.append(DiffRow('ins', None, j1 + k + 1, ins_lines[k], ins_spans))
            adds += 1

        for k in range(paired, len(del_lines)):
            rows.append(DiffRow('del', i1 + k + 1, None, del_lines[k]))
            dels += 1

        for k in range(paired, len(ins_lines)):
            rows.append(DiffRow('ins', None, j1 + k + 1, ins_lines[k]))
            adds += 1

    return rows, adds, dels


def collapse_unchanged(rows: list[DiffRow], context: int = DIFF_CONTEXT) -> list[DiffRow]:
    """Replace long runs of 'eq' rows with a single 'skip' row, keeping
    `context` unchanged lines on either side of every change region.
    """
    change_indices = [i for i, r in enumerate(rows) if r.tag != 'eq']

    if not change_indices:
        return rows

    keep: set[int] = set()

    for ci in change_indices:
        keep.add(ci)

        for k in range(1, context + 1):

            if ci - k >= 0:
                keep.add(ci - k)

            if ci + k < len(rows):
                keep.add(ci + k)

    out: list[DiffRow] = []
    i = 0
    n = len(rows)

    while i < n:

        if i in keep:
            out.append(rows[i])
            i += 1
            continue

        skip_start = i

        while i < n and i not in keep:
            i += 1

        skip_count = i - skip_start
        plural = 's' if skip_count != 1 else ''
        out.append(DiffRow(
            tag='skip',
            a_lineno=None,
            b_lineno=None,
            line=f'⋯ {skip_count} unchanged line{plural} ⋯',
        ))

    return out


def _append_inline(
    body: Text,
    line: str,
    spans: list[tuple[int, int]] | None,
    base_style: str,
    hi_style: str,
) -> None:
    """Append `line` to `body`, painting changed-char spans with `hi_style`
    and the rest with `base_style`. With no spans, the whole line uses
    `base_style`.
    """
    if not spans:
        body.append(line, style=base_style)
        return

    cursor = 0

    for start, end in spans:

        if start > cursor:
            body.append(line[cursor:start], style=base_style)

        body.append(line[start:end], style=hi_style)
        cursor = end

    if cursor < len(line):
        body.append(line[cursor:], style=base_style)


def render_diff_panel(
    path: str,
    before: str,
    after: str,
    content_width: int,
) -> RenderableType:
    """Render the diff body as a rounded Padding-wrapped Panel.

    `content_width` is the width available for the panel itself (already
    accounting for DIFF_INDENT). Each changed row is padded with trailing
    spaces to fill its row, so the red/green backgrounds extend all the way
    to the right edge of the panel.
    """
    rows, adds, dels = compute_diff_rows(before, after)
    rows = collapse_unchanged(rows)

    max_a = max((r.a_lineno or 0 for r in rows), default=1)
    max_b = max((r.b_lineno or 0 for r in rows), default=1)
    gutter_w = max(2, len(str(max(max_a, max_b))))

    # Prefix layout: "{tag} {a:>w} │ {b:>w} " → 2w + 6 chars
    prefix_w = 2 * gutter_w + 6

    # Panel borders (2) + padding (2*1) eat 4 cols. Subtract one safety col
    # so Rich never has to wrap the padded bg row, which would visually
    # break the highlight strip.
    row_width = max(prefix_w + 5, content_width - 4 - 1)

    title = Text()
    title.append('diff', style='bold')
    title.append(' → ', style='dim')
    title.append(path, style='cyan')
    title.append('   ')
    title.append(f'+{adds}', style='green')
    title.append(' ', style='dim')
    title.append(f'-{dels}', style='red')

    blank = ' ' * gutter_w
    body = Text()

    for row in rows:

        if row.tag == 'skip':
            placeholder = row.line.center(row_width)
            body.append(f'{placeholder}\n', style=DIFF_SKIP_STYLE)
            continue

        a_str = str(row.a_lineno) if row.a_lineno is not None else blank
        b_str = str(row.b_lineno) if row.b_lineno is not None else blank

        if row.tag == 'eq':
            prefix = f'  {a_str:>{gutter_w}} │ {b_str:>{gutter_w}} '
            body.append(f'{prefix}{row.line}\n', style=DIFF_EQ_STYLE)
            continue

        # spans is not None means the line was paired with its counterpart
        # and processed for char-level diff — render dim base + bright hi
        # spans. spans is None means whole-line tint (full rewrite or
        # unpaired insert/delete).
        if row.tag == 'del':
            tag_char = '-'
            base_style = DIFF_DEL_DIM_STYLE if row.spans is not None else DIFF_DEL_STYLE
            hi_style = DIFF_DEL_HI_STYLE
        else:
            tag_char = '+'
            base_style = DIFF_INS_DIM_STYLE if row.spans is not None else DIFF_INS_STYLE
            hi_style = DIFF_INS_HI_STYLE

        prefix = f'{tag_char} {a_str:>{gutter_w}} │ {b_str:>{gutter_w}} '
        body.append(prefix, style=base_style)

        _append_inline(body, row.line, row.spans, base_style, hi_style)

        padding = max(0, row_width - prefix_w - len(row.line))
        body.append(f'{" " * padding}\n', style=base_style)

    panel = Panel(
        body,
        title=title,
        title_align='left',
        border_style='dim',
        box=ROUNDED,
        padding=(0, 1),
        expand=True,
    )

    return Padding(panel, (0, 0, 0, DIFF_INDENT))


def format_result_summary(cell: 'ToolCell') -> str:
    """Status-side tail: '28 lines · 0.4s', 'exit 1 · 0.2s', 'running… · 1.3s'."""
    if cell.status == 'running':
        if cell.started_at is None:
            return 'running…'

        return f'running… · {format_duration(time.monotonic() - cell.started_at)}'

    duration_part = ''

    if cell.started_at is not None and cell.ended_at is not None:
        duration_part = f' · {format_duration(cell.ended_at - cell.started_at)}'

    if cell.result is None:
        return f'done{duration_part}'

    if cell.status == 'error':
        first_line = (cell.result.splitlines() or ['error'])[0]

        return f'{truncate_oneline(first_line, 40)}{duration_part}'

    line_count = cell.result.count('\n') + 1 if cell.result else 0

    if line_count > 1:
        return f'{line_count} lines{duration_part}'

    return f'{len(cell.result)} chars{duration_part}'

#     ================================
# --> Cells
#     ================================


class Cell(ABC):
    """Base cell. Subclasses implement render(width) → call self._finalize(ansi).

    `_finalize` stores the ANSI text AND pre-parses it into prompt_toolkit
    fragments. The OutputPanel reads `cell.fragments` directly on the hot path
    instead of re-parsing ANSI on every cache miss.
    """

    ansi: str = ''
    fragments: list[tuple[str, str]] = []

    @abstractmethod
    def render(self, width: int) -> None: ...

    def _finalize(self, ansi: str) -> None:
        self.ansi = ansi
        self.fragments = parse_ansi_to_fragments(ansi)


@dataclass
class HeaderCell(Cell):
    """Welcome banner: 'CODING AGENT' in block letters with model/cwd/tools.

    Rendered once at TUI startup so the first user message has visible top
    spacing instead of crowding the terminal's top edge.
    """

    provider: str
    model: str
    cwd: str
    tools: tuple[str, ...] = ()
    started_at: datetime = field(default_factory=datetime.now)
    ansi: str = field(default='', init=False)

    def render(self, width: int) -> None:
        banner = Text(render_title('ATLAS'), style=f'bold {TITLE_COLOR}')

        info = Text()
        info.append('model  ', style='dim')
        info.append(f'{self.provider}/{self.model}\n', style='cyan')
        info.append('cwd    ', style='dim')
        info.append(f'{self.cwd}\n')
        info.append('tools  ', style='dim')
        info.append(f'{", ".join(self.tools) if self.tools else "(none)"}\n', style='green')
        info.append('time   ', style='dim')
        info.append(self.started_at.strftime('%A, %B %d, %Y · %H:%M'), style='magenta')

        layout = Table.grid(padding=(0, 4))
        layout.add_column(vertical='middle', no_wrap=True)
        layout.add_column(vertical='middle')
        layout.add_row(banner, info)

        panel = Panel(
            layout,
            border_style='dim',
            box=ROUNDED,
            padding=(1, 3),
            expand=False,
        )

        self._finalize(render_to_ansi(panel, width))


@dataclass
class UserCell(Cell):
    text: str
    ansi: str = field(default='', init=False)

    def render(self, width: int) -> None:
        renderable = Text('▎ ', style='cyan') + Text(self.text)

        self._finalize(render_to_ansi(renderable, width))


@dataclass
class AssistantCell(Cell):
    reasoning: str = ''
    content: str = ''
    done: bool = False
    interrupted: bool = False
    # Collapsible reasoning. Live-shown while streaming; once `done`, the
    # reasoning hides behind a clickable header that toggles this flag.
    reasoning_expanded: bool = False
    # Stable id used by the OutputPanel to route clicks back to this cell.
    cell_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ansi: str = field(default='', init=False)
    _last_render_t: float = field(default=0.0, init=False)  # throttles streaming renders

    def is_empty(self) -> bool:
        return not self.reasoning and not self.content

    def render(self, width: int) -> None:
        parts: list[RenderableType] = []

        if self.reasoning:
            # Reasoning is "finished" the moment the model starts emitting
            # content (or the turn ends with reasoning-only). Collapse as soon
            # as that flips so the user's eyes track to the answer.
            reasoning_finished = bool(self.content) or self.done

            if not reasoning_finished:
                parts.append(Text(self.reasoning, style='dim italic'))
            else:
                arrow = '▾' if self.reasoning_expanded else '▸'
                parts.append(Text(f'{arrow} thinking', style='dim'))

                if self.reasoning_expanded:
                    parts.append(Text(self.reasoning, style='dim italic'))

                if self.content:
                    parts.append(Text(''))  # blank line so dropdown reads as a header

        if self.content:
            parts.append(Markdown(self.content))

        if not self.done and (self.reasoning or self.content):
            parts.append(Text('▍', style='bold'))

        if self.interrupted:
            parts.append(Text('[interrupted]', style='dim red'))

        self._finalize(render_to_ansi(Group(*parts), width) if parts else '')


@dataclass
class ToolCell(Cell):
    name: str
    args_json: str
    tool_call_id: str
    result: str | None = None
    status: ToolStatus = 'running'
    expanded: bool = False
    started_at: float | None = None
    ended_at: float | None = None
    # Diff attachment — populated by ToolHandler for file mutators. When set,
    # a `▸ diff` sub-arrow appears under the args row. Default collapsed; the
    # user expands it explicitly.
    diff_path: str | None = None
    diff_before: str | None = None
    diff_after: str | None = None
    diff_expanded: bool = False
    ansi: str = field(default='', init=False)

    def has_diff(self) -> bool:
        return self.diff_before is not None and self.diff_after is not None

    def render(self, width: int) -> None:
        color = STATUS_COLOR[self.status]
        arrow = '⮟' if self.expanded else '⮞'
        glyph = STATUS_GLYPH[self.status]

        primary = extract_primary_arg(self.args_json)
        summary = format_result_summary(self)

        header = Text()
        header.append(f'{arrow}  ', style=color)
        header.append(self.name, style='bold')

        if primary:
            header.append(' → ', style='dim')
            header.append(primary, style=color)

        header.append('   ')
        header.append(f'{glyph} {summary}', style=color)

        parts: list[RenderableType] = [header]

        if self.args_json and self.args_json.strip() not in ('', '{}'):
            sub = Text()
            sub.append('  ⤷ ', style='dim')
            sub.append(
                truncate_oneline(self.args_json, ARGS_LINE_TRUNCATE),
                style='dim',
            )
            parts.append(sub)

        if self.has_diff():
            diff_arrow = '▾' if self.diff_expanded else '▸'

            _, adds, dels = compute_diff_rows(self.diff_before or '', self.diff_after or '')

            diff_header = Text()
            diff_header.append('  ', style='dim')
            diff_header.append(f'{diff_arrow} ', style='cyan')
            diff_header.append('diff', style='cyan')
            diff_header.append('   ')
            diff_header.append(f'+{adds}', style='green')
            diff_header.append(' ', style='dim')
            diff_header.append(f'-{dels}', style='red')

            parts.append(diff_header)

            if self.diff_expanded:
                content_width = max(20, width - DIFF_INDENT)
                parts.append(render_diff_panel(
                    self.diff_path or '',
                    self.diff_before or '',
                    self.diff_after or '',
                    content_width,
                ))

        if self.expanded and self.result is not None:
            indented = '\n'.join(
                f'{RESULT_INDENT}{line}' for line in self.result.splitlines()
            )
            parts.append(Text(''))
            parts.append(Text(indented))

        self._finalize(render_to_ansi(Group(*parts), width))


@dataclass
class ErrorCell(Cell):
    message: str
    ansi: str = field(default='', init=False)

    def render(self, width: int) -> None:
        panel = Panel(
            Text(self.message, style='red'),
            title='error',
            border_style='red',
            title_align='left',
            expand=True,
        )

        self._finalize(render_to_ansi(panel, width))
