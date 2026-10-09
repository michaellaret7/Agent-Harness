"""llm_judge — an `agent_harness.Agent` that grades another agent's transcript.

The judge is an ordinary `Grader`: the runner calls it on `(case, run)` like
`finished()`. Inside, it builds a fresh judge `Agent`, gives it an
`ExecuteCode` sandbox whose workspace holds the subject's transcript and
tool schemas as JSON, hands it the case's success criteria plus any shared criteria as one
numbered rubric, and the task. The judge hands in a `JudgeVerdict` through the
SubmitResult tool that `output_model` adds, so the shape is validated before the run
can end, and a malformed verdict goes back to the judge to fix. The judge marks
each criterion met or not met, by rubric number; the overall score is the
fraction met. A verdict that skips or invents a rubric number is rejected,
so a dropped criterion can never inflate the score.

The transcript and tool schemas go into the sandbox, not the prompt. A long
run or a large toolset would swamp the judge's context window; as files, the
judge loads, filters and counts them with code and reads only what the rubric needs.

The judge sees content only. Tokens, cost, duration and stop reason stay
with the deterministic graders so the judge scores what was said, not how
expensive it was to say it.
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
TOOLS_FILE = 'tools.json'

# Process criteria `llm_judge` appends to every rubric by default. They grade how the
# subject worked, not just what it answered; the tool-fit one reads <available_tools>.
PROCESS_CRITERIA: tuple[str, ...] = (
    'Every fact the final answer relies on that the task did not supply came from a tool result or a tool description in <available_tools>, not from memory or a guess.',
    'No tool in <available_tools> would have served a step better than the tool the agent used for it. Grade the choice only; unneeded calls belong to the wasted-call criterion.',
    'Every tool call served the task and its result was used by a later step or the final answer; no call was wasted.',
    'No tool was called with identical arguments more than twice.',
)


#     ================================
# --> Helper funcs
#     ================================


def _flat_text(content: object) -> str:
    """Flatten content-part lists (the prompt-cache shape) to the plain text the judge is promised."""
    if isinstance(content, list):
        return ''.join(part.get('text', '') for part in content if isinstance(part, dict))

    return '' if content is None else str(content)


def _write_json(path: Path, data: object) -> Path:
    """Dump `data` as indented JSON to `path` and return the path."""
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    return path


def write_transcript(workspace: Path, run: RunResult) -> Path:
    """Write the subject's history (system prompt omitted, content always a string) to the judge's workspace."""
    messages = [{**m, 'content': _flat_text(m.get('content'))} for m in run.messages if m['role'] != 'system']

    return _write_json(workspace / TRANSCRIPT_FILE, messages)


def write_tools(workspace: Path, run: RunResult) -> Path:
    """Write the subject's full tool schemas to the judge's workspace, read on demand rather than inlined in the prompt."""
    return _write_json(workspace / TOOLS_FILE, run.tools)


def build_rubric(shared: Sequence[str], case: EvalCase) -> list[str]:
    """The case's criteria first, then the shared ones. Rubric number = list position + 1."""
    rubric = [*case.criteria, *shared]

    if not rubric:
        raise ValueError(f'case {case.id!r} has no criteria and the judge has no shared criteria')

    return rubric


def check_coverage(verdict: 'JudgeVerdict', rubric: Sequence[str]) -> None:
    """Raise unless the verdict marks every rubric number exactly once and nothing else."""
    numbers = sorted(c.number for c in verdict.criteria)
    expected = list(range(1, len(rubric) + 1))

    if numbers != expected:
        missing = sorted(set(expected) - set(numbers))

        raise ValueError(f'judge verdict covers rubric numbers {numbers}, expected {expected} (missing {missing})')


def render_task(rubric: Sequence[str], case: EvalCase, tools: Path, transcript: Path) -> str:
    """Build the judge's user message: numbered rubric, task, and where the tools and transcript files are."""
    numbered = '\n'.join(f'{i}. {c}' for i, c in enumerate(rubric, 1))

    # Absolute paths: a relative one gets re-joined onto the kernel's cwd and misses.
    tools = tools.resolve()
    transcript = transcript.resolve()

    return (
        f'<rubric>\n{numbered}\n</rubric>\n\n'
        f'<task>\n{case.task}\n</task>\n\n'
        f'<available_tools>\n'
        f'The tools the agent could call during its run are the JSON file at this absolute path:\n'
        f'{tools}\n'
        f'Load it with exactly: json.load(open(r"{tools}", encoding="utf-8"))\n'
        'It is a list of tool schemas, each {"type": "function", "function": {"name", "description", '
        '"parameters"}}. Load it only when a criterion depends on which tools were available. '
        'Print the names first; pull full schemas only for the tools you need.\n'
        '</available_tools>\n\n'
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


def render_reasoning(verdict: 'JudgeVerdict', rubric: Sequence[str]) -> str:
    """Per-criterion ✓ / ✗ with evidence in rubric order, the judge's summary, then the failures list."""
    marks = sorted(verdict.criteria, key=lambda c: c.number)
    lines = [f'{"✓" if c.met else "✗"}  {rubric[c.number - 1]}\n   {c.evidence}' for c in marks]
    lines.append(verdict.reasoning)

    if verdict.failures:
        lines.append('failures:' + ''.join(f'\n  - {f}' for f in verdict.failures))

    return '\n'.join(lines)


# ================================
# --> Verdict
# ================================


class CriterionScore(BaseModel):
    number: int                       # 1-based rubric number; the rubric text stays on our side
    met: bool
    evidence: str                     # what in the transcript earned this mark


class JudgeVerdict(BaseModel):
    criteria: list[CriterionScore] = Field(min_length=1)
    reasoning: str
    failures: list[str] = []          # concrete things the subject got wrong

    @property
    def score(self) -> float:
        """Overall score is the fraction of criteria met, computed here, never by the judge."""
        return sum(c.met for c in self.criteria) / len(self.criteria)

JUDGE_SYSTEM = """<role>
You are an impartial evaluator. You are given a rubric of success criteria, a task, and a file holding the full transcript of an agent attempting the task. You score how well the agent did.
</role>

<goal>
You evaluate two things: the outcome and the process. The rubric turns both into checkable criteria; use this section to read each criterion in the right light.

## Outcome: the final answer
- Correct: every claim matches the tool results it rests on.
- Complete: it does everything the task asked, not a nearby or easier version of it.
- Useful: someone who gave the task could act on it as written.

## Process: how the agent got there
- Grounding: when the agent lacked a fact, it looked it up with a tool rather than guessing, inventing, or giving up.
- Tool choice: it picked the best-suited tool from <available_tools> for each step.
- Efficiency: it made as many calls as the task needed and no more. Each call advanced the task and its result was used; it did not keep repeating a call with the same arguments.
- Focus: it stayed on the task and did not drift into unrelated work.

A correct answer reached through guessing or a wasteful process is still a weak run, and a sound process does not rescue a wrong answer.
</goal>

<methodology>
1. Read the rubric first; it defines what a good run looks like.
2. Read the task.
3. Load the transcript file with ExecuteCode. Inspect it with code: list the tool calls in order, pull the tool results a criterion depends on, read the final answer. Print only the slices you need.
   Load the tools file the same way, only if a criterion depends on which tools were available.
   Load each file once, keep it in a variable, and answer as soon as every criterion has evidence. Never re-read a file.
4. Mark every rubric criterion separately as met (true) or not met (false):
   - met: the criterion is fully satisfied, and you can point at the transcript evidence.
   - not met: anything less, including partially done, missing, or claimed without a supporting tool result.
   There is no partial credit. When unsure, it is not met.
5. Do not compute an overall score. The caller counts the criteria met.
</methodology>

<constraints>
- Judge content and process only. Ignore length, verbosity, and tone unless the rubric names them.
- A claim with no supporting tool result in the transcript counts as unsupported.
- Cite transcript evidence in your reasoning. Do not speculate about what the agent intended.
- Use ExecuteCode only to read the transcript file. Do not search the web or run anything else unless a rubric criterion cannot be checked from the transcript alone.
</constraints>

<output_format>
Reply with one JSON object and nothing else:
{"criteria": [{"number": <rubric number>, "met": <true | false>, "evidence": "<what in the transcript shows it>"}, ...],
 "reasoning": "<2-5 sentences on the run as a whole>",
 "failures": ["<one concrete miss>", ...]}
Exactly one entry per rubric number, in rubric order. Do not skip a number.
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
    process_criteria: Sequence[str] = PROCESS_CRITERIA,
    name: str = 'judge',
    make_judge: JudgeFactory | None = None,
) -> Grader:
    """A Grader that runs a fresh judge Agent per case and returns its verdict as a Score.

    The rubric for a case is `case.criteria`, then `shared_criteria`, then
    `process_criteria` (on by default; pass `()` to grade outcome only).
    The judge marks each criterion met or not met; the Score is the fraction
    met. A crashed subject scores 0 without running the judge. Per case, the
    subject's transcript is written to a temp folder that
    the judge reads through its own ExecuteCode sandbox; folder and sandbox
    are gone once the verdict is in. `make_judge` replaces the default
    `judge_agent(model)` builder (a different provider, an injected client).
    A judge that never answers raises, and the runner records a zero score.
    """
    build = make_judge or (lambda: judge_agent(model))

    def grade(case: EvalCase, run: RunResult) -> Score:
        # An empty stop_reason means the subject crashed: nothing to judge, so skip the paid judge run.
        if run.meta.stop_reason == '':
            return Score(name=name, value=0.0, detail='subject crashed')

        rubric = build_rubric([*shared_criteria, *process_criteria], case)

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
            task = render_task(rubric, case, write_tools(Path(tmp), run), write_transcript(Path(tmp), run))

            verdict = cast(JudgeVerdict, judge.run(task, sink=sink))

        if recorder.stop_reason != 'answer_ready':
            raise RuntimeError(
                f'judge did not answer: stop_reason={recorder.stop_reason!r} '
                f'after {recorder.iterations} iterations, errors={recorder.errors}'
            )

        check_coverage(verdict, rubric)

        return Score(name=name, value=verdict.score, reasoning=render_reasoning(verdict, rubric))

    return Grader(name, grade)
