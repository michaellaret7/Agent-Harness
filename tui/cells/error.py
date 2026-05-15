"""ErrorCell — red-bordered panel for an error string."""
from __future__ import annotations

from dataclasses import dataclass, field

from rich.panel import Panel
from rich.text import Text

from tui.cells.base import Cell, render_to_ansi


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
