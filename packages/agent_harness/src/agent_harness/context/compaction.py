"""Context compaction: keep a long-running agent's history under the model's context window.

When the live history nears `threshold × context_window`, the older span is archived
to disk, replaced by a rolling summary, and the recent tail is kept word for word.
Full design: `docs/agents/context_compaction.md`.

This module holds the pure building blocks: the config, a cheap token estimate, and
the cut-point search that keeps every tool_call/tool_result pair on one side.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CompactionConfig:
    """When and how much to compact. Fractions are of the model's context window.

    Args:
        threshold: Compact once the estimated prompt reaches this fraction of the window.
        keep_recent: Token budget, as a fraction of the window, for the verbatim tail.
        context_window: The model's max context in tokens; None asks the provider once.
        summary_max_tokens: Output cap for the summarizer call.
        archive_dir: Where archived segments go; None keeps no archive.
            `AgentWorkspace.attach` points it at the sandbox.
    """

    threshold: float = 0.80
    keep_recent: float = 0.25
    context_window: int | None = None
    summary_max_tokens: int = 4_000
    archive_dir: Path | None = None

    def __post_init__(self) -> None:
        if not 0 < self.keep_recent < self.threshold < 1:
            raise ValueError(
                f'CompactionConfig: need 0 < keep_recent < threshold < 1, '
                f'got keep_recent={self.keep_recent}, threshold={self.threshold}'
            )

        if self.context_window is not None and self.context_window <= 0:
            raise ValueError(f'CompactionConfig: context_window must be positive, got {self.context_window}')


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token count: serialized JSON characters / 4.

    Only used where the provider's exact count is unavailable: messages appended
    since the last model call, and sizing the verbatim tail.
    """
    return sum(len(json.dumps(message, ensure_ascii=False)) for message in messages) // 4


def find_cut(messages: list[dict[str, Any]], tail_budget: int) -> int:
    """Return the index where the verbatim tail starts.

    Walks backwards from the end, keeping messages while they fit in
    `tail_budget` tokens, then moves forward past any `tool` messages so the tail
    never opens mid tool-exchange (a tool result without its assistant call is
    rejected by the API). Never returns 0: the system message is not part of
    either side. A result of `len(messages)` means an empty tail.
    """
    cut = len(messages)
    tail_tokens = 0

    while cut > 1:
        size = estimate_tokens([messages[cut - 1]])

        if tail_tokens + size > tail_budget:
            break

        cut -= 1
        tail_tokens += size

    while cut < len(messages) and messages[cut]['role'] == 'tool':
        cut += 1

    return cut
