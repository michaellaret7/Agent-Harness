"""Conversation cells: pure data + Rich-based render to ANSI.

Each cell is a dataclass holding raw state. `render(width)` produces an ANSI
string, stores it on `self.ansi`, AND pre-parses it into prompt_toolkit
fragments cached on `self.fragments`. Doing the parse here (worker thread)
keeps the UI thread's scroll path off the ANSI parser; the OutputPanel just
concatenates pre-parsed fragments per frame.

Background color is never set — terminal inherits its own theme.
"""
from __future__ import annotations

import io
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
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
    ansi: str = field(default='', init=False)
    _last_render_t: float = field(default=0.0, init=False)  # throttles streaming renders

    def is_empty(self) -> bool:
        return not self.reasoning and not self.content

    def render(self, width: int) -> None:
        parts: list[RenderableType] = []

        if self.reasoning:
            parts.append(Text(self.reasoning, style='dim italic'))

            if self.content:
                parts.append(Text('─── thinking ───', style='dim'))

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
    ansi: str = field(default='', init=False)

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
            header.append('  → ', style='dim')
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
