"""EvalCase pairs a subject task with criteria used only by the judge.

Criteria are concrete, checkable statements; reference answers can be criteria.
Only the task reaches the subject. llm_judge combines case and shared criteria.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    id: str
    task: str
    criteria: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
