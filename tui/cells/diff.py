"""File-diff engine — Cursor-style inline word diff under a ToolCell.

This module owns: row computation (paired del/ins with char-level spans),
unchanged-region collapsing, and the Rich rendering of the final panel.
Only ToolCell uses it; lives as a cells sibling so the algorithm stays out
of the cell file proper.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Literal

from rich.box import ROUNDED
from rich.console import RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

#     ================================
# --> Helper funcs and constants
#     ================================

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
