"""llm_judge — an `agent_harness.Agent` that grades another agent's transcript.

The judge is an ordinary `Grader`: the runner calls it on `(case, run)` like
`finished()`. Inside, it builds a fresh judge `Agent`, gives it an
`ExecuteCode` sandbox whose workspace holds the subject's transcript as
JSON, hands it the case's success criteria plus any shared criteria as one
numbered rubric, the task, and parses the JSON verdict the judge writes. The judge scores
each criterion on a five-step scale; the overall score is their mean.

The transcript goes into the sandbox, not the prompt. A long run would
swamp the judge's context window; as a file, the judge loads, filters and
counts it with code and reads only what the rubric needs.

The judge sees content only. Tokens, cost, duration and stop reason stay
with the deterministic graders so the judge scores what was said, not how
expensive it was to say it.

The verdict is parsed from the judge's final text rather than through
`Agent(output_model=...)`: that path makes a second model call per case
and the judge prompt already pins the JSON shape.
"""
from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
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


@contextmanager
def judge_workspace(runs_dir: Path | None, case_id: str) -> Iterator[Path]:
    """A kept folder `runs_dir/<case id>/` when `runs_dir` is given; otherwise a temp dir removed on exit."""
    if runs_dir is not None:
        workspace = runs_dir / case_id
        workspace.mkdir(parents=True, exist_ok=True)

        yield workspace

        return

    with tempfile.TemporaryDirectory(prefix=f'judge_{case_id}_', ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)


def write_verdict(workspace: Path, name: str, verdict: 'JudgeVerdict') -> Path:
    """Keep the judge's verdict beside the transcript it was reached from."""
    path = workspace / f'{name}.verdict.json'

    with path.open('w', encoding='utf-8') as f:
        f.write(verdict.model_dump_json(indent=2))

    return path


def render_task(shared: Sequence[str], case: EvalCase, transcript: Path) -> str:
    """Build the judge's user message: numbered criteria (case first, then shared), task, and where the transcript is."""
    criteria = [*case.criteria, *shared]

    if not criteria:
        raise ValueError(f'case {case.id!r} has no criteria and the judge has no shared criteria')

    rubric = '\n'.join(f'{i}. {c}' for i, c in enumerate(criteria, 1))

    # Absolute path: a relative one gets re-joined onto the kernel's cwd and misses.
    return (
        f'<rubric>\n{rubric}\n</rubric>\n\n'
        f'<task>\n{case.task}\n</task>\n\n'
        f'<transcript>\n'
        f'The full transcript is the JSON file at this absolute path, which already exists:\n'
        f'{transcript.resolve()}\n'
        f'Load it with exactly: json.load(open(r"{transcript.resolve()}", encoding="utf-8"))\n'
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


def parse_verdict(text: str) -> 'JudgeVerdict':
    """Extract the first JSON object from the judge's reply and validate it."""
    match = re.search(r'\{.*\}', text, re.DOTALL)

    if match is None:
        raise ValueError(f'judge returned no JSON object: {text[:200]!r}')

    return JudgeVerdict.model_validate(json.loads(match.group(0)))


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


def judge_agent(model: str, provider: str = 'openrouter', max_iters: int = 25) -> Agent:
    """Build a judge Agent with the judge system prompt and a short iteration cap."""
    
    return Agent(
        provider=provider,
        model=model,
        system=JUDGE_SYSTEM,
        max_iters=max_iters,
    )


def llm_judge(
    model: str | None = None,
    shared_criteria: Sequence[str] = (),
    provider: str = 'openrouter',
    name: str = 'judge',
    runs_dir: Path | None = None,
    make_judge: JudgeFactory | None = None,
) -> Grader:
    """A Grader that runs a fresh judge Agent per case and returns its verdict as a Score.

    `model` is the judge model; a fresh `judge_agent(model, provider)` is
    built per case. `make_judge` replaces that builder for callers that
    need a judge with extra tools or an injected client (tests). Exactly
    one of the two must be given.

    The rubric for a case is `case.criteria` followed by `shared_criteria`
    (house rules that apply to every task: grounded figures, no duplicate
    tool calls). The judge scores each one on a five-step scale and the
    overall score is their mean, so a run that meets three of four
    criteria lands near 0.75 rather than 0 or 1.
    The subject's transcript is written as JSON into a
    per-case workspace; the grader attaches its own `ExecuteCode` tool
    backed by a sandbox rooted there and closes that sandbox once the
    verdict is in. By default the workspace is a temp dir removed after
    the verdict (the transcript already lives in Langfuse when tracing is
    on). Pass `runs_dir` to keep `runs_dir/<case id>/transcript.json` and
    `<name>.verdict.json` on disk instead. A judge that fails to answer or returns malformed JSON
    raises, and the runner records that as a zero score with the traceback.
    """
    if (model is None) == (make_judge is None):
        raise ValueError('llm_judge: pass exactly one of model= or make_judge=')

    build = make_judge if make_judge is not None else (lambda: judge_agent(cast(str, model), provider))

    def grade(case: EvalCase, run: RunResult) -> Score:
        with judge_workspace(runs_dir, case.id) as workspace:
            transcript = write_transcript(workspace, run)

            # Per-case sandbox: closed on exit so a batch does not accumulate kernels.
            with SubprocessSandbox(workspace) as sandbox:
                judge = build()
                judge.add_tool(bind_tool(execute_code, _sandbox=sandbox))

                # Record the judge's own run so a missing verdict names its cause (max_iters, error).
                recorder = EvalSink()
                sink = cast(Sink, MultiSink([recorder, LogSink(f'{case.id}.{name}')]))

                reply = judge.run(render_task(shared_criteria, case, transcript), sink=sink)

            if recorder.stop_reason != 'answer_ready':
                raise RuntimeError(
                    f'judge did not answer: stop_reason={recorder.stop_reason!r} '
                    f'after {recorder.iterations} iterations, errors={list(recorder.errors)}'
                )

            verdict = parse_verdict(cast(str, reply))

            if runs_dir is not None:
                write_verdict(workspace, name, verdict)

        reasoning = render_reasoning(verdict)

        return Score(name=name, value=verdict.score, reasoning=reasoning)

    return Grader(name, grade)
