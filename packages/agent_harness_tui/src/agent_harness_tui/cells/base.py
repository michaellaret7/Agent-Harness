"""Cell ABC and shared ANSI rendering helpers.

Each cell holds raw state plus a `render(width)` method that produces ANSI
text and a pre-parsed list of prompt_toolkit fragments. The pre-parse runs
on the worker thread so the UI thread's hot path never touches the ANSI
parser. Background color is never set — the terminal's native theme shows
through.
"""
from __future__ import annotations

import io
import threading
from abc import ABC, abstractmethod

from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from rich.console import Console, RenderableType

#     ================================
# --> Helper funcs
#     ================================


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


_LOCK_INIT_GUARD = threading.Lock()

#     ================================
# --> Cell ABC
#     ================================


class Cell(ABC):
    """Base cell. Subclasses implement render(width) → call self._finalize(ansi).

    `_finalize` stores the ANSI text AND pre-parses it into prompt_toolkit
    fragments. The OutputPanel reads `cell.fragments` directly on the hot path
    instead of re-parsing ANSI on every cache miss.

    `render_lock` serializes concurrent render() calls on the same cell. The
    worker thread (streaming deltas, tool result updates) and the UI thread
    (toggle clicks on arrows) can otherwise race inside Rich, producing torn
    fragment lists or raising mid-render. History acquires this lock around
    every mutate-then-render path. Lazy-initialized so dataclass children
    don't need to declare it themselves.
    """

    ansi: str = ''
    fragments: list[tuple[str, str]] = []

    @property
    def render_lock(self) -> threading.Lock:
        lock: threading.Lock | None = getattr(self, '_render_lock', None)

        if lock is None:
            # Double-checked init under a class-level guard so the first two
            # concurrent callers can't each create their own lock.
            with _LOCK_INIT_GUARD:
                lock = getattr(self, '_render_lock', None)

                if lock is None:
                    lock = threading.Lock()
                    object.__setattr__(self, '_render_lock', lock)

        return lock

    @abstractmethod
    def render(self, width: int) -> None: ...

    def _finalize(self, ansi: str) -> None:
        self.ansi = ansi
        self.fragments = parse_ansi_to_fragments(ansi)
