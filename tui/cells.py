"""Conversation cells: pure data + Rich-based render to ANSI.

Each cell is a dataclass holding raw state. `render(width)` produces an ANSI
string and stores it on `self.ansi`. The renderer just walks the History and
joins `cell.ansi`.

Background color is never set — terminal inherits its own theme.
"""
from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

#     ================================
# --> Helper funcs
#     ================================

ToolStatus = Literal['running', 'ok', 'error']

ARGS_TRUNCATE = 80
RESULT_LINES_TRUNCATE = 20

BORDER_BY_STATUS: dict[ToolStatus, str] = {
    'running': 'yellow',
    'ok': 'green',
    'error': 'red',
}


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


def truncate_oneline(text: str, limit: int) -> str:
    """Collapse to one line and truncate."""
    flat = text.replace('\n', ' ').strip()

    if len(flat) <= limit:
        return flat

    return flat[:limit] + '…'


def truncate_lines(text: str, limit: int) -> str:
    """Keep first `limit` lines, append `… [+N lines]` marker."""
    lines = text.splitlines()

    if len(lines) <= limit:
        return text

    head = '\n'.join(lines[:limit])

    return f'{head}\n… [+{len(lines) - limit} lines]'

#     ================================
# --> Cells
#     ================================


class Cell(ABC):
    """Base cell. Subclasses implement render(width) -> mutates self.ansi."""

    ansi: str = ''

    @abstractmethod
    def render(self, width: int) -> None: ...


@dataclass
class UserCell(Cell):
    text: str
    ansi: str = field(default='', init=False)

    def render(self, width: int) -> None:
        renderable = Text('▎ ', style='cyan') + Text(self.text)

        self.ansi = render_to_ansi(renderable, width)


@dataclass
class AssistantCell(Cell):
    reasoning: str = ''
    content: str = ''
    done: bool = False
    interrupted: bool = False
    ansi: str = field(default='', init=False)

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

        self.ansi = render_to_ansi(Group(*parts), width) if parts else ''


@dataclass
class ToolCell(Cell):
    name: str
    args_json: str
    tool_call_id: str
    result: str | None = None
    status: ToolStatus = 'running'
    ansi: str = field(default='', init=False)

    def render(self, width: int) -> None:
        body_parts: list[RenderableType] = [
            Text(truncate_oneline(self.args_json, ARGS_TRUNCATE), style='dim'),
        ]

        if self.result is not None:
            body_parts.append(Text(truncate_lines(self.result, RESULT_LINES_TRUNCATE)))
        elif self.status == 'running':
            body_parts.append(Text('running…', style='yellow'))

        panel = Panel(
            Group(*body_parts),
            title=f'tool: {self.name}',
            border_style=BORDER_BY_STATUS[self.status],
            title_align='left',
            expand=True,
        )

        self.ansi = render_to_ansi(panel, width)


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

        self.ansi = render_to_ansi(panel, width)
