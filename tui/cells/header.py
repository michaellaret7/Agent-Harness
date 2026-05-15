"""HeaderCell — welcome banner shown once at TUI startup.

Renders 'ATLAS' in ANSI-Shadow figlet block letters alongside a metadata
column (model, cwd, tools, time). Lives in its own file because the
title-art data is bulky and only used here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tui.cells.base import Cell, render_to_ansi

#     ================================
# --> Helper funcs: ANSI-Shadow block-letter banner
#     ================================

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

#     ================================
# --> Cell
#     ================================


@dataclass
class HeaderCell(Cell):
    """Welcome banner: 'ATLAS' in block letters with model/cwd/tools.

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
