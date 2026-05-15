"""ToolCell — header + args row + inline diff + collapsible result body.

The tool-status taxonomy (running/ok/error), the args-row formatting, and
the result-summary tail all live here because they're only ever consumed
by this cell. The actual diff rendering lives in cells/diff.py.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal

from rich.console import Group, RenderableType
from rich.text import Text

from tui.cells.base import Cell, render_to_ansi
from tui.cells.diff import DIFF_INDENT, compute_diff_rows, render_diff_panel

#     ================================
# --> Helper funcs and constants
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
# --> Cell
#     ================================


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
