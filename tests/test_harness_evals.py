"""harness_evals contracts. Run: uv run --all-packages pytest tests/test_harness_evals.py"""
from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
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
from harness_evals.judge import write_transcript
from harness_evals.runner import AgentFactory


#     ================================
# --> Helper funcs
#     ================================


@dataclass(frozen=True)
class Completion:
    """A non-streamed reply: what the structured-output parse call receives."""

    content: str


def completion_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        'id': 'completion-test', 'object': 'chat.completion', 'created': 0, 'model': 'contract-model',
        'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}],
    })


@contextmanager
def scripted_factory(
    responses: Sequence[Sequence[StreamItem] | Completion],
    tools: Sequence | None = None,
) -> Iterator[tuple[AgentFactory, ScriptedProvider]]:
    """Yield a factory that builds fresh agents sharing one in-memory provider.

    Streamed calls consume the next stream script; a non-streamed call (the
    judge's structured-output parse) consumes the next `Completion`.
    """
    provider = ScriptedProvider(responses)  # type: ignore[arg-type]

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)

        if body.get('stream'):
            return provider.respond(request)

        provider.requests.append(body)
        item = responses[len(provider.requests) - 1]
        assert isinstance(item, Completion), 'a non-streamed request needs a scripted Completion'

        return completion_response(item.content)

    with pytest.MonkeyPatch.context() as environment:
        environment.setenv('LANGFUSE_PUBLIC_KEY', '')

        with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
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


def test_llm_judge_reads_transcript_from_sandbox() -> None:
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
        Completion(verdict),                                                                      # parse into JudgeVerdict
    ]
    case = EvalCase('add_case', 'Add 2 and 3.', criteria=('correct sum',))

    with scripted_factory(responses, arithmetic_tools(invoked)) as (make_agent, api):
        grader = llm_judge('contract-model', shared_criteria=['units stated'], make_judge=make_agent)
        (record,) = run_evals(make_agent, [case], [grader])

    (score,) = record.scores
    assert (score.name, score.value) == ('judge', 0.75), 'mean of 1.0 and 0.5'
    assert '1.00  correct sum' in score.reasoning and '0.50  units stated' in score.reasoning
    assert 'Used add' in score.reasoning and '  - no units' in score.reasoning
    assert score.detail == ''

    judge_prompt = api.requests[2]['messages'][-1]['content']
    assert '<rubric>\n1. correct sum\n2. units stated' in judge_prompt, 'case criteria first, shared after'
    assert '<task>\nAdd 2 and 3.' in judge_prompt
    assert 'transcript.json' in judge_prompt and 'judge_add_case_' in judge_prompt, 'absolute path into the temp folder'
    assert 'The sum is 5' not in judge_prompt, 'the transcript must not be inlined into the prompt'

    # The structured-output call converts the judge's final text, not anything else.
    assert api.requests[4]['messages'][-1]['content'].startswith('Verdict: ')
    assert api.requests[4]['response_format']['json_schema']['name'] == 'JudgeVerdict'

    # The sandbox really served the file: the judge's code printed the subject's final answer.
    sandbox_result = api.requests[3]['messages'][-1]
    assert sandbox_result['role'] == 'tool'
    assert 'The sum is 5' in message_text(sandbox_result)

    # The judge's temp folder is gone once the verdict is in.
    assert not any(Path(tempfile.gettempdir()).glob('judge_add_case_*'))


def test_write_transcript_flattens_content(tmp_path: Path) -> None:
    """The file the judge reads drops the system prompt and always has string content."""
    messages = [
        {'role': 'system', 'content': 'secret system prompt'},
        {'role': 'user', 'content': 'Add 2 and 3.'},
        {'role': 'tool', 'tool_call_id': 'sum', 'content': [{'type': 'text', 'text': '5', 'cache_control': {'type': 'ephemeral'}}]},
    ]
    run = RunResult(final='5', messages=messages, meta=EvalSink().meta)

    transcript = json.loads(write_transcript(tmp_path, run).read_text(encoding='utf-8'))

    assert [m['role'] for m in transcript] == ['user', 'tool']
    assert transcript[1]['content'] == '5'


def test_llm_judge_malformed_reply_scores_zero() -> None:
    """A verdict that fails the JudgeVerdict schema is a grader failure: zero score, traceback in reasoning."""
    responses = [[{'content': 'hello'}], [{'content': 'I cannot decide.'}], Completion('{"reasoning": "unsure"}')]

    with scripted_factory(responses) as (make_agent, _):
        grader = llm_judge('contract-model', shared_criteria=['any criterion'], make_judge=make_agent)
        (record,) = run_evals(make_agent, [EvalCase('greet', 'Say hello.')], [grader])

    (score,) = record.scores
    assert score.value == 0.0
    assert 'grader raised' in score.reasoning and 'criteria' in score.reasoning


def test_report_shows_marks_and_judge_reasoning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The table shows ✓ / ✗ / decimals per grader; every judge score is followed by its reasoning; JSON lands in runs/."""
    verdict = ('{"criteria": [{"criterion": "correct", "score": 1.0, "evidence": "says 5"},'
               ' {"criterion": "units", "score": 0.2, "evidence": "none"}],'
               ' "reasoning": "Answered 5 but gave no units.", "failures": ["no units"]}')
    responses = [
        [{'content': 'The sum is 5'}],                                                        # subject
        [tool_delta(tool_call('ExecuteCode', {'code': 'print(1)'}, 'read'))], [{'content': verdict}],   # judge
        Completion(verdict),                                                                             # parse
    ]
    graders = [finished(), max_iterations(1)]

    with scripted_factory(responses) as (make_agent, _):
        graders.append(llm_judge('contract-model', make_judge=make_agent))
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


@pytest.mark.parametrize('crashed', [False, True])
def test_report_preserves_run_details(tmp_path: Path, crashed: bool) -> None:
    """YAML preserves tool order, scores, usage, and errors for completed and crashed runs."""
    invoked: list[tuple[str, int, int]] = []
    responses = [
        [tool_delta(tool_call('add', {'a': 2, 'b': 3}, 'sum'))],
        [tool_delta(tool_call('multiply', {'a': 5, 'b': 4}, 'product'))],
    ]

    if not crashed:
        responses.append([{'content': 'The result is 20'}])

    case = EvalCase('calculation', 'Add 2 and 3, then multiply by 4.', criteria=('result is 20',))

    with scripted_factory(responses, arithmetic_tools(invoked)) as (make_agent, _):
        records = run_evals(make_agent, [case], [finished()])

    run_dir = write_report(records, tmp_path)
    report = yaml.safe_load((run_dir / 'calculation.yaml').read_text(encoding='utf-8'))
    errors = list(records[0].run.meta.errors)
    stop_reason = '' if crashed else 'answer_ready'

    assert bool(errors) == crashed
    assert report == {
        'id': 'calculation',
        'scores': [{'name': 'finished', 'value': 0.0}] if crashed else [
            {'name': 'finished', 'value': 1.0, 'detail': 'answer_ready'},
        ],
        'task': case.task,
        'criteria': ['result is 20'],
        'final': '' if crashed else 'The result is 20',
        'stop_reason': stop_reason,
        'iterations': 0 if crashed else 3,
        'usage': {
            'prompt_tokens': 0, 'completion_tokens': 0, 'reasoning_tokens': 0,
            'cached_tokens': 0, 'cache_write_tokens': 0, 'cost': 0.0,
        },
        'tool_calls': ['add', 'multiply'],
        'errors': errors,
    }

