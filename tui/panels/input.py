"""InputPanel — multi-line entry framed with rounded corners.

The frame is hand-rolled from Window primitives because prompt_toolkit's
stock `Frame` hardcodes square corners. PlaceholderProcessor injects dim
hint text on line 0 of an empty buffer; once the user types, it falls
back to the unchanged fragment stream.
"""
from __future__ import annotations

from functools import partial
from typing import Any

from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout.containers import HSplit, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput
from prompt_toolkit.widgets import TextArea

#     ================================
# --> Placeholder processor
#     ================================


class PlaceholderProcessor(Processor):
    """Append dim placeholder text after the prompt when the buffer is empty.

    Applies only to line 0 — the prompt's line. Once the buffer has any text
    the fragments pass through unchanged, so the placeholder disappears as
    the user types.
    """

    def __init__(self, text: str, style: str = '') -> None:
        self.text = text
        self.style = style

    def apply_transformation(self, transformation_input: TransformationInput) -> Transformation:
        ti = transformation_input

        if ti.lineno != 0 or ti.document.text:
            return Transformation(ti.fragments)

        return Transformation(list(ti.fragments) + [(self.style, self.text)])

#     ================================
# --> Input panel
#     ================================


class InputPanel:
    """Multi-line text entry with a rounded frame and vertical breathing room.

    The frame is hand-rolled from Window primitives because prompt_toolkit's
    stock `Frame` hardcodes square corners. A 1-row blank pad above and below
    the TextArea centers the prompt vertically. The input grows from 1 to
    MAX_LINES rows as the user types past the visible content; longer input
    scrolls within the area.
    """

    MIN_LINES = 1
    MAX_LINES = 4
    PLACEHOLDER = 'Ask Atlas anything…'
    PROMPT_STR = '   > '

    def __init__(self) -> None:
        self.history = InMemoryHistory()

        # dont_extend_height pins the body's max to its content height.
        # Reason: without it, the parent HSplit absorbs spare rows into the
        # body (max=4), pushing the cursor to the top of an oversized box and
        # leaving the bottom padding visually larger than the top.
        self.area = TextArea(
            height=Dimension(min=self.MIN_LINES, max=self.MAX_LINES),
            multiline=True,
            wrap_lines=True,
            history=self.history,
            prompt=self.PROMPT_STR,
            dont_extend_height=True,
            get_line_prefix=self._continuation_prefix,
            input_processors=[
                PlaceholderProcessor(self.PLACEHOLDER, style='class:input.placeholder'),
            ],
        )

        self.container = self._build_rounded_frame(self.area)

    @classmethod
    def _continuation_prefix(cls, lineno: int, wrap_count: int) -> str:
        """Indent wrap continuations and subsequent logical lines.

        prompt_toolkit renders the `prompt` only on line 0/wrap 0. Every other
        visual row needs leading spaces so wrapped text and Shift+Enter newlines
        hang under the cursor instead of resetting to the left border.
        """
        if lineno == 0 and wrap_count == 0:
            return ''

        return ' ' * len(cls.PROMPT_STR)

    @staticmethod
    def _build_rounded_frame(body: Any) -> HSplit:
        fill = partial(Window, style='class:frame.border')

        top = VSplit([
            fill(width=1, height=1, char='╭'),
            fill(char='─', height=1),
            fill(width=1, height=1, char='╮'),
        ], height=1)

        padded_body = HSplit([
            Window(height=1),
            body,
            Window(height=1),
        ])

        middle = VSplit([
            fill(width=1, char='│'),
            padded_body,
            fill(width=1, char='│'),
        ])

        bottom = VSplit([
            fill(width=1, height=1, char='╰'),
            fill(char='─', height=1),
            fill(width=1, height=1, char='╯'),
        ], height=1)

        return HSplit([top, middle, bottom])

    @property
    def text(self) -> str:
        return self.area.text

    def clear(self) -> None:
        self.area.text = ''
