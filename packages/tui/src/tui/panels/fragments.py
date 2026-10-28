"""Fragment helpers for OutputPanel: arrow glyphs, click handlers, and cell spacing.

attach_arrow_handler binds only the first matching arrow; cell_separator
chooses spacing between adjacent cells. These helpers have no scroll state.
"""
from __future__ import annotations

from typing import Any, Callable

from prompt_toolkit.mouse_events import MouseEvent

from tui.cells import Cell, ErrorCell, HeaderCell, UserCell

#     ================================
# --> Constants
#     ================================

TOOL_ARROW_CHARS = ('⮞', '⮟')
ASSISTANT_ARROW_CHARS = ('▸', '▾')

# Cells that mark a turn boundary — anything that introduces or interrupts an
# agent turn. Adjacent cells in this set get a full blank line of breathing
# room; everything else (assistant↔tool within a turn) stacks tight.
TURN_BOUNDARY_TYPES = (UserCell, HeaderCell, ErrorCell)

#     ================================
# --> Helper funcs
#     ================================


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
    space, if any). Other fragments — including ones that already carry a
    handler from a previous attach_arrow_handler call — are preserved
    verbatim so chained calls compose.
    """
    result: list = []
    attached = False

    for entry in fragments:
        style, text = entry[0], entry[1]

        if attached:
            result.append(entry)
            continue

        idx = -1

        for ch in arrows:
            i = text.find(ch)

            if i != -1 and (idx == -1 or i < idx):
                idx = i

        if idx == -1:
            result.append(entry)
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
