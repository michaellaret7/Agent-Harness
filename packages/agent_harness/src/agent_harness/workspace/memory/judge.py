"""Classify related memories for the curator.

    keep       preserve distinct facts
    merge      combine reworded facts
    supersede  delete outdated facts

A Judge maps memories to a Decision. LlmJudge validates one JSON response per
cluster; invalid responses raise and leave the cluster unchanged.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, field_validator, model_validator

from agent_harness.client import build_client
from agent_harness.workspace.memory.store import IMPORTANCE, MEMORY_KINDS, Memory

if TYPE_CHECKING:
    from openai import OpenAI

# Small and cheap: one short call per cluster, at most MAX_JUDGE_CALLS per pass
JUDGE_MODEL = 'deepseek/deepseek-v4.1-flash'

JUDGE_PROMPT = f"""
<role>
You curate an AI agent's long-term memory. You are shown a few related memories, oldest first,
and decide whether they should stay as they are.
</role>

<methodology>
Pick exactly one action:
- keep: they are different facts, even if on the same subject (e.g. gross margin vs operating margin). This is the default; choose it whenever unsure.
- merge: they state the same fact in different words. Give `ids` (at least two) and one self-contained `content`
  that keeps every detail from them, plus its `kind` and `importance`.
- supersede: a newer memory makes an older one wrong or outdated (e.g. a fair value that was later revised).
  Give `ids` of the outdated memories only; the newest version is never in `ids`.
</methodology>

<constraints>
- Use only ids shown to you.
- kind is one of: {', '.join(MEMORY_KINDS)}.
- importance is one of: {', '.join(IMPORTANCE)}. For a merge, use the highest importance among the merged memories.
- Never drop a number, date, ticker or condition when merging.
</constraints>

<output_format>
Reply with one JSON object and nothing else:
{{"action": "keep" | "merge" | "supersede", "ids": [int], "content": str | null, "kind": str | null, "importance": str | null}}
</output_format>
"""


class Decision(BaseModel):
    """The judge's verdict on one cluster. `ids` are the memories the action applies to."""

    action: Literal['keep', 'merge', 'supersede']
    ids: list[int] = []
    content: str | None = None
    kind: str | None = None
    importance: str | None = None

    @field_validator('ids', mode='before')
    @classmethod
    def _no_ids(cls, ids: list[int] | None) -> list[int]:
        """Models often answer a keep with `"ids": null`; that means no ids."""
        return [] if ids is None else ids

    @model_validator(mode='after')
    def _complete(self) -> Decision:
        """A merge needs its rewrite; a supersede needs something to delete."""
        if self.action == 'merge':
            if len(self.ids) < 2 or not (self.content or '').strip():
                raise ValueError('merge needs at least two ids and the merged content')

            if self.kind not in MEMORY_KINDS or self.importance not in IMPORTANCE:
                raise ValueError(f'merge needs a kind from {MEMORY_KINDS} and an importance from {tuple(IMPORTANCE)}')

        if self.action == 'supersede' and not self.ids:
            raise ValueError('supersede needs the ids of the outdated memories')

        return self


Judge = Callable[[list[Memory]], Decision]


#     ================================
# --> Helper funcs
#     ================================


def _render(memories: list[Memory]) -> str:
    """One line per memory, oldest first: id, date saved, kind, importance, content."""
    lines = []

    for memory in memories:
        saved = time.strftime('%Y-%m-%d', time.localtime(memory.created_at))
        lines.append(f'[{memory.id}] saved {saved} | {memory.kind} | {memory.importance} | {memory.content}')

    return '\n'.join(lines)


def _check_ids(decision: Decision, memories: list[Memory]) -> None:
    """Raise when the decision names ids outside the cluster, or would supersede every memory in it."""
    shown = {memory.id for memory in memories}
    unknown = set(decision.ids) - shown

    if unknown:
        raise ValueError(f'judge named ids outside the cluster: {sorted(unknown)}')

    if decision.action == 'supersede' and set(decision.ids) == shown:
        raise ValueError('supersede must leave the current memory in place')


#     ================================
# --> Judge
#     ================================


class LlmJudge:
    """A Judge backed by one JSON-mode chat call. The client is built on first use, like `Agent` and `ApiEmbedder`."""

    def __init__(self, model: str = JUDGE_MODEL, provider: str = 'openrouter') -> None:
        self.model = model
        self.provider = provider
        self._client: OpenAI | None = None

    def __call__(self, memories: list[Memory]) -> Decision:
        """Decide what the cluster is. Raises on an answer that is not valid JSON, not a valid Decision or off-cluster."""
        if self._client is None:
            self._client, _ = build_client(self.provider, self.model)

        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {'role': 'system', 'content': JUDGE_PROMPT},
                {'role': 'user', 'content': _render(memories)},
            ],
            response_format={'type': 'json_object'},
        )

        decision = Decision.model_validate_json(response.choices[0].message.content or '')
        _check_ids(decision, memories)

        return decision
