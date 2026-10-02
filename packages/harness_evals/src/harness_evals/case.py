"""EvalCase — one task the subject agent is asked to perform, and what success looks like.

`criteria` are the success criteria for this task: concrete statements
the judge can check against the transcript ("names three risks, each with
a cited source"). A reference answer is just one more criterion ("the
answer states 391"). The subject agent never sees `criteria` — only `task`
reaches it. The judge scores every case criterion plus any shared
criteria passed to `llm_judge`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    id: str
    task: str
    criteria: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
