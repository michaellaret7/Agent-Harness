"""Explicit tool return contract, independent of output rendering and dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ToolResult:
    """Model-facing payload and execution status returned by every tool.

    Payload text never determines status. Dispatch adds timing and handles
    denied/interrupted calls separately through the sink's ToolOutcome.
    """

    payload: str
    status: Literal['ok', 'error']

    def __post_init__(self) -> None:
        if not isinstance(self.payload, str):
            raise TypeError('ToolResult.payload must be a string')

        if self.status not in ('ok', 'error'):
            raise ValueError("ToolResult.status must be 'ok' or 'error'")
