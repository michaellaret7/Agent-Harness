"""Grader, Score, and the shipped deterministic graders.

A grader is a named callable that maps `(case, run) -> Score`. `run` is a
`RunResult`: the subject's final answer, its message history, and the
`RunMetadata` the sink recorded. Deterministic graders read mostly `run.meta`
(numbers the judge should never see); the LLM judge in `judge.py` reads
mostly `run.messages`.

Each shipped grader is a small factory returning a `Grader`, so a consumer's
grader list reads as configuration: `[finished(), max_cost(0.05)]`. A
consumer's own grader is `Grader('revenue', revenue_match)`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from harness_evals.case import EvalCase
from harness_evals.sink import RunMetadata


#     ================================
# --> Helper funcs
#     ================================


def _passed(name: str, ok: bool, detail: str) -> 'Score':
    return Score(name=name, value=1.0 if ok else 0.0, detail=detail)


#     ================================
# --> Types
#     ================================


@dataclass(frozen=True)
class RunResult:
    """Everything one subject run produced, handed to every grader."""

    final: str                 # what Agent.run returned
    messages: list[dict]       # the subject's context window
    meta: RunMetadata              # what only the sink saw


@dataclass(frozen=True)
class Score:
    name: str
    value: float               # 0.0–1.0; pass/fail graders emit 0 or 1
    detail: str = ''           # one line: the number compared, the tool that failed
    reasoning: str = ''        # prose: why a judge scored what it scored; empty for deterministic graders


@dataclass(frozen=True)
class Grader:
    name: str
    fn: Callable[[EvalCase, RunResult], Score]

    def __call__(self, case: EvalCase, run: RunResult) -> Score:
        return self.fn(case, run)


#     ================================
# --> Deterministic graders
#     ================================


def finished() -> Grader:
    """Pass when the loop stopped because the model answered, not by limit or crash."""
    def grade(case: EvalCase, run: RunResult) -> Score:
        return _passed('finished', run.meta.stop_reason == 'answer_ready', run.meta.stop_reason)

    return Grader('finished', grade)


def no_tool_errors() -> Grader:
    """Pass when every tool dispatch ended with status 'ok'."""
    def grade(case: EvalCase, run: RunResult) -> Score:
        failed = [name for name, outcome in run.meta.tool_outcomes if outcome.status != 'ok']

        return _passed('no_tool_errors', not failed, ', '.join(failed))

    return Grader('no_tool_errors', grade)


def max_iterations(limit: int) -> Grader:
    """Pass when the run used at most `limit` loop iterations."""
    def grade(case: EvalCase, run: RunResult) -> Score:
        return _passed('max_iterations', run.meta.iterations <= limit, f'{run.meta.iterations}/{limit}')

    return Grader('max_iterations', grade)


def max_cost(usd: float) -> Grader:
    """Pass when the run's reported cost is at most `usd`. vLLM reports 0."""
    def grade(case: EvalCase, run: RunResult) -> Score:
        cost = run.meta.usage.cost

        return _passed('max_cost', cost <= usd, f'${cost:.4f} <= ${usd:.4f}')

    return Grader('max_cost', grade)


def tools_called(*names: str) -> Grader:
    """Pass when every named tool was called at least once."""
    def grade(case: EvalCase, run: RunResult) -> Score:
        called = {name for name, _ in run.meta.tool_outcomes}

        return _passed('tools_called', set(names) <= called, f'called={sorted(called)}')

    return Grader('tools_called', grade)


def final_answer_contains(substring: str) -> Grader:
    """Pass when the final answer contains `substring` (case-insensitive)."""
    def grade(case: EvalCase, run: RunResult) -> Score:
        ok = substring.lower() in run.final.lower()

        return _passed('final_answer_contains', ok, substring)

    return Grader('final_answer_contains', grade)
