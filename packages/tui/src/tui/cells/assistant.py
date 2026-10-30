"""Streaming assistant output with collapsible reasoning.

    streaming / awaiting Markdown -> direct plain-text fragments
    done and markdown_ready       -> Rich Markdown from a background render
    interrupted                   -> plain text with an interruption marker

Keep Rich off the streaming path to avoid starving the UI event loop.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text

from tui.cells.base import Cell, render_to_ansi

# prompt_toolkit style strings used by the fast streaming path. Chosen to
# match the colors the Rich path uses so the visual switch when Markdown
# lands is subtle.
_FAST_REASONING_STYLE = 'italic fg:ansibrightblack'
_FAST_HEADER_STYLE = 'fg:ansibrightblack'
_FAST_CURSOR_STYLE = 'bold'
_FAST_INTERRUPTED_STYLE = 'fg:ansired'


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
    # Flipped to True by the background Markdown worker once the heavy Rich
    # render lands. Until then, even `done=True` cells render via the fast
    # plain-text path so the worker thread can move on to the next tool call
    # without waiting on Markdown.
    markdown_ready: bool = False
    ansi: str = field(default='', init=False)
    _last_render_t: float = field(default=0.0, init=False)  # throttles streaming renders

    def is_empty(self) -> bool:
        return not self.reasoning and not self.content

    def render(self, width: int) -> None:
        """Pick the right render path for the cell's current state.

        Fast path while streaming OR while the background Markdown worker
        hasn't finished yet. Full Rich Markdown once `markdown_ready` flips.
        Toggling `reasoning_expanded` re-runs whichever path matches the
        cell's done state — expanding reasoning during streaming stays cheap.
        """
        if self.done and self.markdown_ready:
            self._render_full(width)
            return

        self._render_fast()

    def _render_fast(self) -> None:
        """Build prompt_toolkit fragments directly. No Rich. No ANSI parse.

        Mirrors the visual structure of the full path closely enough that the
        eventual swap to Markdown reads as a quiet upgrade rather than a
        re-layout. Width is irrelevant here — prompt_toolkit's wrap_lines
        handles wrapping at draw time.
        """
        fragments: list[tuple[str, str]] = []

        if self.reasoning:
            reasoning_finished = bool(self.content) or self.done

            if not reasoning_finished:
                fragments.append((_FAST_REASONING_STYLE, self.reasoning))
            else:
                arrow = '▾' if self.reasoning_expanded else '▸'
                fragments.append((_FAST_HEADER_STYLE, f'{arrow} thinking'))

                if self.reasoning_expanded:
                    fragments.append(('', '\n'))
                    fragments.append((_FAST_REASONING_STYLE, self.reasoning))

                if self.content:
                    fragments.append(('', '\n\n'))

        if self.content:
            fragments.append(('', self.content))

        if not self.done and (self.reasoning or self.content):
            fragments.append(('', '\n'))
            fragments.append((_FAST_CURSOR_STYLE, '▍'))

        if self.interrupted:
            fragments.append(('', '\n'))
            fragments.append((_FAST_INTERRUPTED_STYLE, '[interrupted]'))

        # `ansi` is left as a sentinel so version-keyed cache invalidation
        # logic that still inspects it stays happy. The OutputPanel's
        # _ensure_fresh skips cells based on fragments emptiness, not ansi.
        self.ansi = ' ' if fragments else ''
        self.fragments = fragments

    def _render_full(self, width: int) -> None:
        """Heavy Rich path: Markdown for content, styled blocks for reasoning."""
        parts: list[RenderableType] = []

        if self.reasoning:
            arrow = '▾' if self.reasoning_expanded else '▸'
            parts.append(Text(f'{arrow} thinking', style='dim'))

            if self.reasoning_expanded:
                parts.append(Text(self.reasoning, style='dim italic'))

            if self.content:
                parts.append(Text(''))  # blank separator before content

        if self.content:
            parts.append(Markdown(self.content))

        if self.interrupted:
            parts.append(Text('[interrupted]', style='dim red'))

        self._finalize(render_to_ansi(Group(*parts), width) if parts else '')
