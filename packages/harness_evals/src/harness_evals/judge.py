"""llm_judge — an `agent_harness.Agent` that grades another agent's transcript.

The judge is an ordinary `Grader`: the runner calls it on `(case, run)` like
`finished()`. Inside, it builds a fresh judge `Agent`, gives it an
`ExecuteCode` sandbox whose workspace holds the subject's transcript as
JSON, hands it the case's success criteria plus any shared criteria as one
numbered rubric, and the task. The judge returns a `JudgeVerdict` through
`Agent(output_model=...)`, so the shape is guaranteed rather than parsed. The judge scores
each criterion on a five-step scale; the overall score is their mean.

The transcript goes into the sandbox, not the prompt. A long run would
swamp the judge's context window; as a file, the judge loads, filters and
counts it with code and reads only what the rubric needs.

The judge sees content only. Tokens, cost, duration and stop reason stay
with the deterministic graders so the judge scores what was said, not how
expensive it was to say it.

Structured output costs one extra small model call per case: the engine
maps the judge's final text into `JudgeVerdict` after the judge finishes.
"""
from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from agent_harness import Agent
from agent_harness.base_tools.code_execution.sandbox import SubprocessSandbox
from agent_harness.base_tools.code_execution.tool import execute_code
from agent_harness.sinks import LogSink, MultiSink, Sink
from agent_harness.tooling.decorator import bind_tool
from pydantic import BaseModel, Field

from harness_evals.case import EvalCase
from harness_evals.grader import Grader, RunResult, Score
from harness_evals.sink import EvalSink

JudgeFactory = Callable[[], Agent]

TRANSCRIPT_FILE = 'transcript.json'


#     ================================
# --> Helper funcs
#     ================================


def _flat_text(content: object) -> str:
    """Flatten content-part lists (the prompt-cache shape) to the plain text the judge is promised."""
    if isinstance(content, list):
        return ''.join(part.get('text', '') for part in content if isinstance(part, dict))

    return '' if content is None else str(content)


def write_transcript(workspace: Path, run: RunResult) -> Path:
    """Write the subject's history (system prompt omitted, content always a string) to the judge's workspace."""
    path = workspace / TRANSCRIPT_FILE
    messages = [{**m, 'content': _flat_text(m.get('content'))} for m in run.messages if m['role'] != 'system']

    with path.open('w', encoding='utf-8') as f:
        json.dump(messages, f, indent=2)

    return path


def render_task(shared: Sequence[str], case: EvalCase, transcript: Path) -> str:
    """Build the judge's user message: numbered criteria (case first, then shared), task, and where the transcript is."""
    criteria = [*case.criteria, *shared]

    if not criteria:
        raise ValueError(f'case {case.id!r} has no criteria and the judge has no shared criteria')

    rubric = '\n'.join(f'{i}. {c}' for i, c in enumerate(criteria, 1))

    # Absolute path: a relative one gets re-joined onto the kernel's cwd and misses.
    transcript = transcript.resolve()

    return (
        f'<rubric>\n{rubric}\n</rubric>\n\n'
        f'<task>\n{case.task}\n</task>\n\n'
        f'<transcript>\n'
        f'The full transcript is the JSON file at this absolute path, which already exists:\n'
        f'{transcript}\n'
        f'Load it with exactly: json.load(open(r"{transcript}", encoding="utf-8"))\n'
        'It is a list of chat messages in order. Each has "role" (user | assistant | tool) and '
        '"content"; assistant messages may carry "tool_calls" (id, function.name, function.arguments); '
        'tool messages carry "tool_call_id" linking the result to its call. The last assistant message '
        'is the final answer.\n'
        'Inspect it with code. Do not print the whole file. Do not search the filesystem for it.\n'
        '</transcript>'
    )


def render_reasoning(verdict: 'JudgeVerdict') -> str:
    """Per-criterion scores with evidence, the judge's summary, then the failures list."""
    lines = [f'{c.score:.2f}  {c.criterion}\n      {c.evidence}' for c in verdict.criteria]
    lines.append(verdict.reasoning)

    if verdict.failures:
        lines.append('failures:' + ''.join(f'\n  - {f}' for f in verdict.failures))

    return '\n'.join(lines)


#     ================================
# --> Verdict
#     ================================


class CriterionScore(BaseModel):
    criterion: str
    score: float = Field(ge=0.0, le=1.0)
    evidence: str                     # what in the transcript earned this score


class JudgeVerdict(BaseModel):
    criteria: list[CriterionScore] = Field(min_length=1)
    reasoning: str
    failures: list[str] = []          # concrete things the subject got wrong

    @property
    def score(self) -> float:
        """Overall score is the mean of the criterion scores, computed here, never by the judge."""
        return sum(c.score for c in self.criteria) / len(self.criteria)

# TODO: REVIEW THIS SYSTEM PROMPT AND IRON OUT HOW THE JUDGE SCORES AND EVIDENCE FOR EACH CRITERION.

JUDGE_SYSTEM = """<role>
You are an impartial evaluator. You are given a rubric of success criteria, a task, and a file holding the full transcript of an agent attempting the task. You score how well the agent did.
</role>

<methodology>
1. Read the rubric first; it defines what a good run looks like.
2. Read the task.
3. Load the transcript file with ExecuteCode. Inspect it with code: list the tool calls in order, pull the tool results a criterion depends on, read the final answer. Print only the slices you need.
   Budget: aim for 3 to 5 ExecuteCode calls in total. Load once, keep the list in a variable, and answer as soon as every criterion has evidence. Never re-read the file.
4. Score every rubric criterion separately, on this scale:
   1.00  fully met, with transcript evidence
   0.75  met, with a minor gap you can name
   0.50  partially met: real progress, real omission
   0.25  attempted, mostly missed
   0.00  not met, or the claim is fabricated
   Use the whole scale. A criterion only earns 1.00 when you can point at the evidence.
5. Do not compute an overall score. The caller averages the criterion scores.
</methodology>

<constraints>
- Judge content and process only. Ignore length, verbosity, and tone unless the rubric names them.
- A claim with no supporting tool result in the transcript counts as unsupported.
- Cite transcript evidence in your reasoning. Do not speculate about what the agent intended.
- Use ExecuteCode only to read the transcript file. Do not search the web or run anything else unless a rubric criterion cannot be checked from the transcript alone.
</constraints>

<output_format>
Reply with one JSON object and nothing else:
{"criteria": [{"criterion": "<rubric criterion, verbatim>", "score": <0.0-1.0>, "evidence": "<what in the transcript earned it>"}, ...],
 "reasoning": "<2-5 sentences on the run as a whole>",
 "failures": ["<one concrete miss>", ...]}
One entry per rubric criterion, in rubric order.
</output_format>"""


#     ================================
# --> Judge
#     ================================


def judge_agent(model: str, max_iters: int = 25) -> Agent:
    """Build a judge Agent with the judge system prompt and an iteration cap."""
    return Agent(
        model=model, 
        system=JUDGE_SYSTEM, 
        max_iters=max_iters
    )


def llm_judge(
    model: str,
    shared_criteria: Sequence[str] = (),
    name: str = 'judge',
    make_judge: JudgeFactory | None = None,
) -> Grader:
    """A Grader that runs a fresh judge Agent per case and returns its verdict as a Score.

    The rubric for a case is `case.criteria` followed by `shared_criteria`.
    The judge scores each criterion on a five-step scale; the Score is their
    mean. Per case, the subject's transcript is written to a temp folder that
    the judge reads through its own ExecuteCode sandbox; folder and sandbox
    are gone once the verdict is in. `make_judge` replaces the default
    `judge_agent(model)` builder (a different provider, an injected client).
    A judge that never answers raises, and the runner records a zero score.
    """
    build = make_judge or (lambda: judge_agent(model))

    def grade(case: EvalCase, run: RunResult) -> Score:
        # ignore_cleanup_errors: on Windows the stopped kernel can hold the folder open for a moment.
        with (
            tempfile.TemporaryDirectory(prefix=f'judge_{case.id}_', ignore_cleanup_errors=True) as tmp,
            SubprocessSandbox(Path(tmp)) as sandbox,
        ):
            judge = build()
            judge.output_model = JudgeVerdict
            judge.add_tool(bind_tool(execute_code, _sandbox=sandbox))

            # Record the judge's own run so a missing verdict names its cause (max_iters, error).
            recorder = EvalSink()
            sink = cast(Sink, MultiSink([recorder, LogSink(f'{case.id}.{name}')]))
            task = render_task(shared_criteria, case, write_transcript(Path(tmp), run))

            verdict = cast(JudgeVerdict, judge.run(task, sink=sink))

        if recorder.stop_reason != 'answer_ready':
            raise RuntimeError(
                f'judge did not answer: stop_reason={recorder.stop_reason!r} '
                f'after {recorder.iterations} iterations, errors={recorder.errors}'
            )

        return Score(name=name, value=verdict.score, reasoning=render_reasoning(verdict))

    return Grader(name, grade)
