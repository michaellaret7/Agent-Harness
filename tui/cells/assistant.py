"""AssistantCell — streaming model output with collapsible reasoning.

The cell renders three states in order:
  1. live reasoning (dim italic, while the model is still thinking)
  2. collapsed reasoning header (▸/▾ thinking) once content arrives or
     the turn ends
  3. content body (Markdown)

A trailing cursor block (▍) appears while the cell is still streaming;
'[interrupted]' replaces it if the turn was cancelled.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text

from tui.cells.base import Cell, render_to_ansi


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
            # Render plain Text while streaming; Markdown only on `done`.
            # Reason: Rich Markdown is pure-Python and scales superlinearly
            # with content length. Re-parsing the entire message on every
            # 40 ms delta (and growing) holds the GIL so tightly that the
            # prompt_toolkit event loop can't process mouse or scroll
            # events — the TUI feels frozen during long replies. Plain Text
            # is O(len) and cheap; the final Markdown pass happens once in
            # end_assistant.
            if self.done:
                parts.append(Markdown(self.content))
            else:
                parts.append(Text(self.content))

        if not self.done and (self.reasoning or self.content):
            parts.append(Text('▍', style='bold'))

        if self.interrupted:
            parts.append(Text('[interrupted]', style='dim red'))

        self._finalize(render_to_ansi(Group(*parts), width) if parts else '')
