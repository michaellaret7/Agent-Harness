"""run_evals — build a fresh subject agent per case, run it, grade it.

The subject agent is unaware it is being evaluated: it receives only
`case.task`. Everything else (`criteria`, graders) stays on this
side of the boundary. The runner reads `agent.messages` and the sink after
`run()` returns and bundles them into a `RunResult` for the graders.

A fresh agent per case is mandatory — `agent.messages` accumulates across
`run()` calls, so reusing one would leak earlier cases into later prompts.
"""
from __future__ import annotations

import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from agent_harness import Agent
from agent_harness.sinks import LogSink, MultiSink, Sink

from harness_evals.case import EvalCase
from harness_evals.grader import Grader, RunResult, Score
from harness_evals.sink import EvalSink

AgentFactory = Callable[[], Agent]


#     ================================
# --> Helper funcs
#     ================================


def _run_case(make_agent: AgentFactory, case: EvalCase) -> RunResult:
    """Run one case on a fresh agent. A crash still yields a gradable RunResult."""
    agent = make_agent()
    recorder = EvalSink()
    final = ''

    # Every subject run logs under `agent.<case id>` so parallel-case logs stay untangled.
    sink = cast(Sink, MultiSink([recorder, LogSink(case.id)]))

    try:
        result = agent.run(case.task, sink=sink)
        final = result if isinstance(result, str) else result.model_dump_json()

    except Exception as e:
        # Recorded, not raised: one bad case must not kill the batch.
        recorder.on_error(f'subject.crashed {type(e).__name__}: {e}')

    return RunResult(final=final, messages=list(agent.messages), meta=recorder.meta, model=agent.model or '')


def _grade(grader: Grader, case: EvalCase, run: RunResult) -> Score:
    """Apply one grader. A raising grader scores 0 with the traceback as detail."""
    try:
        return grader(case, run)

    except Exception:
        return Score(name=grader.name, value=0.0, detail='grader raised', reasoning='grader raised:\n' + traceback.format_exc())


#     ================================
# --> Records
#     ================================


@dataclass(frozen=True)
class CaseRecord:
    case: EvalCase
    run: RunResult
    scores: tuple[Score, ...]


#     ================================
# --> run_evals
#     ================================


def run_evals(
    make_agent: AgentFactory,
    cases: Sequence[EvalCase],
    graders: Sequence[Grader],
) -> list[CaseRecord]:
    """Run every case sequentially and return one record per case."""
    records: list[CaseRecord] = []

    for case in cases:
        run = _run_case(make_agent, case)

        scores = tuple(_grade(g, case, run) for g in graders)

        records.append(CaseRecord(case=case, run=run, scores=scores))

    return records
