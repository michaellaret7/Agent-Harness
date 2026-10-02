"""harness_evals contracts. Run: uv run --all-packages pytest tests/test_harness_evals.py"""
from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import yaml
from openai import OpenAI

from agent_harness import Agent
from agent_harness.sinks.base import ToolOutcome
from agent_harness.usage import Usage
from contract_support import ScriptedProvider, StreamItem, arithmetic_tools, message_text, tool_call, tool_delta
from harness_evals import (
    EvalCase, EvalSink, Grader, RunResult, Score,
    final_answer_contains, finished, llm_judge, max_iterations, no_tool_errors, print_report, run_evals, tools_called, write_report,
)
from harness_evals.runner import AgentFactory


#     ================================
# --> Helper funcs
#     ================================


@contextmanager
def scripted_factory(
    responses: Sequence[Sequence[StreamItem]],
    tools: Sequence | None = None,
) -> Iterator[tuple[AgentFactory, ScriptedProvider]]:
    """Yield a factory that builds fresh agents sharing one in-memory provider."""
    provider = ScriptedProvider(responses)

    with pytest.MonkeyPatch.context() as environment:
        environment.setenv('LANGFUSE_PUBLIC_KEY', '')

        with httpx.Client(transport=httpx.MockTransport(provider.respond)) as http_client:
            with OpenAI(api_key='test-only', base_url='https://harness.invalid/v1',
                        http_client=http_client, max_retries=0) as client:

                def make_agent() -> Agent:
                    agent = Agent(model='contract-model', tools=list(tools or ()))
                    agent.client = client

                    return agent

                yield make_agent, provider


def scores_by_name(scores: tuple[Score, ...]) -> dict[str, float]:
    return {s.name: s.value for s in scores}


#     ================================
# --> Contract tests
#     ================================


def test_eval_sink_accumulates_meta() -> None:
    """Usage sums across LLM calls; tool outcomes are labelled by name in dispatch order."""
    sink = EvalSink()

    sink.on_usage(Usage(prompt_tokens=10, completion_tokens=5, cost=0.01))
    sink.on_usage(Usage(prompt_tokens=20, completion_tokens=5, cost=0.02))
    sink.on_tool_start('c1', 'add', '{}')
    sink.on_tool_start('c2', 'multiply', '{}')
    sink.on_tool_end('c2', ToolOutcome('20', 'ok', 0.1))
    sink.on_tool_end('c1', ToolOutcome('boom', 'error', 0.2))
    sink.on_error('llm.call_failed x')
    sink.on_loop_end('max_iterations', 3)

    meta = sink.meta

    assert meta.usage == Usage(prompt_tokens=30, completion_tokens=10, cost=0.03)
    assert meta.llm_calls == 2
    assert [name for name, _ in meta.tool_outcomes] == ['multiply', 'add']
    assert [o.status for _, o in meta.tool_outcomes] == ['ok', 'error']
    assert meta.errors == ('llm.call_failed x',)
    assert (meta.stop_reason, meta.iterations, meta.interrupted) == ('max_iterations', 3, False)


def test_run_evals_fresh_agent_per_case() -> None:
    """Each case runs on its own agent, gets its own RunResult, and is graded independently."""
    invoked: list[tuple[str, int, int]] = []
    responses = [
        [tool_delta(tool_call('add', {'a': 2, 'b': 3}, 'sum'))], [{'content': 'The sum is 5'}],
        [{'content': 'hello'}],
    ]
    cases = [EvalCase('add_case', 'Add 2 and 3.'), EvalCase('greet_case', 'Say hello.')]
    graders = [finished(), no_tool_errors(), tools_called('add'), final_answer_contains('5'), max_iterations(1)]

    with scripted_factory(responses, arithmetic_tools(invoked)) as (make_agent, api):
        records = run_evals(make_agent, cases, graders)

    assert [r.case.id for r in records] == ['add_case', 'greet_case']
    assert invoked == [('add', 2, 3)]

    add_run, greet_run = records[0].run, records[1].run
    assert add_run.final == 'The sum is 5'
    assert [m['role'] for m in add_run.messages] == ['system', 'user', 'assistant', 'tool', 'assistant']
    assert add_run.meta.stop_reason == 'answer_ready'
    assert [name for name, _ in add_run.meta.tool_outcomes] == ['add']

    # The second agent never saw the first case: its history is only its own turn.
    assert greet_run.final == 'hello'
    assert [m['role'] for m in greet_run.messages] == ['system', 'user', 'assistant']
    assert 'Add 2 and 3.' not in str(api.requests[2]['messages'])

    assert scores_by_name(records[0].scores) == {
        'finished': 1.0, 'no_tool_errors': 1.0, 'tools_called': 1.0, 'final_answer_contains': 1.0, 'max_iterations': 0.0,
    }
    assert scores_by_name(records[1].scores) == {
        'finished': 1.0, 'no_tool_errors': 1.0, 'tools_called': 0.0, 'final_answer_contains': 0.0, 'max_iterations': 1.0,
    }


def test_failures_are_recorded_not_raised() -> None:
    """A crashing subject and a crashing grader both land in the record instead of aborting the batch."""
    def exploding(case: EvalCase, run: RunResult) -> Score:
        raise ValueError('grader bug')

    # No scripted responses: the provider asserts on the first request, so the subject crashes.
    with scripted_factory([]) as (make_agent, _):
        records = run_evals(make_agent, [EvalCase('crash', 'Anything.')], [finished(), Grader('exploding', exploding)])

    (record,) = records
    assert record.run.final == ''
    assert record.run.meta.stop_reason == ''
    assert any(e.startswith('subject.crashed') for e in record.run.meta.errors)

    finished_score, exploding_score = record.scores
    assert (finished_score.name, finished_score.value) == ('finished', 0.0)
    assert exploding_score.value == 0.0
    assert 'grader bug' in exploding_score.reasoning and exploding_score.detail == 'grader raised'


def test_llm_judge_reads_transcript_from_sandbox(tmp_path: Path) -> None:
    """The judge gets the transcript as a file in its sandbox, inspects it with ExecuteCode, and its JSON verdict becomes the Score."""
    invoked: list[tuple[str, int, int]] = []
    verdict = ('{"criteria": [{"criterion": "correct sum", "score": 1.0, "evidence": "tool result sum=5, answer says 5"},'
               ' {"criterion": "units stated", "score": 0.5, "evidence": "no units"}],'
               ' "reasoning": "Used add, answered 5.", "failures": ["no units"]}')
    read_final = 'import json; t = json.load(open("transcript.json")); print(t[-1]["content"])'
    responses = [
        [tool_delta(tool_call('add', {'a': 2, 'b': 3}, 'sum'))], [{'content': 'The sum is 5'}],   # subject
        [tool_delta(tool_call('ExecuteCode', {'code': read_final}, 'read'))],                     # judge inspects
        [{'content': 'Verdict: ' + verdict}],                                                     # judge answers
    ]
    case = EvalCase('add_case', 'Add 2 and 3.', criteria=('correct sum',))

    with scripted_factory(responses, arithmetic_tools(invoked)) as (make_agent, api):
        grader = llm_judge(make_judge=make_agent, shared_criteria=['units stated'], runs_dir=tmp_path)
        (record,) = run_evals(make_agent, [case], [grader])

    (score,) = record.scores
    assert (score.name, score.value) == ('judge', 0.75), 'mean of 1.0 and 0.5'
    assert '1.00  correct sum' in score.reasoning and '0.50  units stated' in score.reasoning
    assert 'Used add' in score.reasoning and '  - no units' in score.reasoning
    assert score.detail == ''

    judge_prompt = api.requests[2]['messages'][-1]['content']
    assert '<rubric>\n1. correct sum\n2. units stated' in judge_prompt, 'case criteria first, shared after'
    assert '<task>\nAdd 2 and 3.' in judge_prompt
    assert str((tmp_path / 'add_case' / 'transcript.json').resolve()) in judge_prompt
    assert 'The sum is 5' not in judge_prompt, 'the transcript must not be inlined into the prompt'

    # The sandbox really served the file: the judge's code printed the subject's final answer.
    sandbox_result = api.requests[3]['messages'][-1]
    assert sandbox_result['role'] == 'tool'
    assert 'The sum is 5' in message_text(sandbox_result)

    # The kept workspace holds the evidence and the verdict side by side.
    workspace = tmp_path / 'add_case'
    transcript = json.loads((workspace / 'transcript.json').read_text(encoding='utf-8'))
    assert [m['role'] for m in transcript] == ['user', 'assistant', 'tool', 'assistant']
    assert all(isinstance(m['content'], str) for m in transcript), 'content is always flat text for the judge'
    assert transcript[2]['content'] == '5'
    assert [c['score'] for c in json.loads((workspace / 'judge.verdict.json').read_text(encoding='utf-8'))['criteria']] == [1.0, 0.5]


def test_llm_judge_malformed_reply_scores_zero(tmp_path: Path) -> None:
    """A judge that returns no JSON is a grader failure: zero score, traceback in detail."""
    responses = [[{'content': 'hello'}], [{'content': 'I cannot decide.'}]]

    with scripted_factory(responses) as (make_agent, _):
        grader = llm_judge(make_judge=make_agent, shared_criteria=['any criterion'])   # default: temp workspace
        (record,) = run_evals(make_agent, [EvalCase('greet', 'Say hello.')], [grader])

    (score,) = record.scores
    assert score.value == 0.0
    assert 'no JSON object' in score.reasoning
    assert not list(tmp_path.iterdir()), 'default mode writes nothing under a runs dir'


def test_report_shows_marks_and_judge_reasoning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The table shows ✓ / ✗ / decimals per grader; every judge score is followed by its reasoning; JSON lands in runs/."""
    verdict = ('{"criteria": [{"criterion": "correct", "score": 1.0, "evidence": "says 5"},'
               ' {"criterion": "units", "score": 0.2, "evidence": "none"}],'
               ' "reasoning": "Answered 5 but gave no units.", "failures": ["no units"]}')
    responses = [
        [{'content': 'The sum is 5'}],                                                        # subject
        [tool_delta(tool_call('ExecuteCode', {'code': 'print(1)'}, 'read'))], [{'content': verdict}],   # judge
    ]
    graders = [finished(), max_iterations(1)]

    with scripted_factory(responses) as (make_agent, _):
        graders.append(llm_judge(make_judge=make_agent, runs_dir=tmp_path))
        records = run_evals(make_agent, [EvalCase('add_case', 'Add 2 and 3.', criteria=('correct', 'units'))], graders)

    print_report(records)
    out = capsys.readouterr().out

    assert 'case      finished  max_iterations  judge' in out
    assert 'add_case  ✓         ✓               0.60' in out
    assert '── add_case · judge 0.60' in out
    assert 'Answered 5 but gave no units.' in out
    assert '  - no units' in out

    run_dir = write_report(records, tmp_path)
    assert run_dir.parent == tmp_path and run_dir.name.startswith('run-20'), 'one dated folder per run'
    assert [p.name for p in run_dir.iterdir()] == ['add_case.yaml'], 'one yaml per case'

    text = (run_dir / 'add_case.yaml').read_text(encoding='utf-8')
    report = yaml.safe_load(text)

    assert report['id'] == 'add_case'
    assert report['final'] == 'The sum is 5'
    assert report['tool_calls'] == []
    assert [s['name'] for s in report['scores']] == ['finished', 'max_iterations', 'judge']
    assert report['scores'][2]['reasoning'].startswith('1.00  correct')
    assert 'Answered 5 but gave no units.' in report['scores'][2]['reasoning']
    assert 'messages' not in text and 'transcript' not in text, 'no transcript in the report'
    assert 'reasoning: |' in text, 'multi-line reasoning is a block scalar'

