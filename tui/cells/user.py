"""UserCell — the cyan-prefixed echo of a user prompt."""
from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text

from tui.cells.base import Cell, render_to_ansi


@dataclass
class UserCell(Cell):
    text: str
    ansi: str = field(default='', init=False)

    def render(self, width: int) -> None:
        renderable = Text('▎ ', style='cyan') + Text(self.text)

        self._finalize(render_to_ansi(renderable, width))
