"""run_evals — build a fresh subject agent per case, run it, grade it, optionally several cases at once.

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
from concurrent.futures import ThreadPoolExecutor
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


def _full_tool_schemas(agent: Agent) -> list[dict]:
    """The subject's tool list with every still-deferred stub swapped for its full schema.

    Loaded deferred tools are already promoted in `agent.tools`; unloaded ones
    are stubs (one sentence, empty parameters) whose full dict waits in
    `agent.deferred_tools`. Copies, so the agent's own list is untouched.
    """
    schemas: list[dict] = []

    for entry in agent.tools:
        name = entry['function']['name']
        full = agent.deferred_tools.get(name)

        if full is not None:
            entry = {'type': 'function', 'function': {'name': name, 'description': full['description'], 'parameters': full['parameters']}}

        schemas.append(entry)

    return schemas


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

    return RunResult(
        final=final, messages=list(agent.messages), tools=_full_tool_schemas(agent), meta=recorder.meta, model=agent.model or '',
    )


def _evaluate(make_agent: AgentFactory, case: EvalCase, graders: Sequence[Grader]) -> 'CaseRecord':
    """Run one case and apply every grader to it."""
    run = _run_case(make_agent, case)

    scores = tuple(_grade(g, case, run) for g in graders)

    return CaseRecord(case=case, run=run, scores=scores)


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
    max_workers: int = 1,
) -> list[CaseRecord]:
    """Run every case and return one record per case, in case order.

    `max_workers` cases run at once, each on its own fresh agent; keep it
    low enough for the subject's data APIs' rate limits. The default runs
    cases one after another.
    """
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(lambda case: _evaluate(make_agent, case, graders), cases))
