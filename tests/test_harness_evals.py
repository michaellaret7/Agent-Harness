"""harness_evals contracts. Run: uv run --all-packages pytest tests/test_harness_evals.py"""
from __future__ import annotations

import json
import tempfile
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
    PROCESS_CRITERIA, EvalCase, EvalSink, Grader, RunResult, Score,
    final_answer_contains, finished, llm_judge, max_iterations, no_tool_errors, print_report, run_evals, tools_called, write_report,
)
from harness_evals.judge import write_tools, write_transcript
from harness_evals.runner import AgentFactory


#     ================================
# --> Helper funcs
#     ================================


def submit(verdict: str, call_id: str = 'verdict') -> dict:
    """The judge handing in its verdict JSON through the SubmitResult tool."""

    return tool_delta(tool_call('SubmitResult', json.loads(verdict), call_id))


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


def test_llm_judge_reads_transcript_from_sandbox() -> None:
    """The judge gets the transcript as a file in its sandbox, inspects it with ExecuteCode, and its JSON verdict becomes the Score."""
    invoked: list[tuple[str, int, int]] = []
    verdict = ('{"criteria": [{"number": 1, "met": true, "evidence": "tool result sum=5, answer says 5"},'
               ' {"number": 2, "met": false, "evidence": "no units"}],'
               ' "reasoning": "Used add, answered 5.", "failures": ["no units"]}')
    read_final = 'import json; t = json.load(open("transcript.json")); print(t[-1]["content"])'
    responses = [
        [tool_delta(tool_call('add', {'a': 2, 'b': 3}, 'sum'))], [{'content': 'The sum is 5'}],   # subject
        [tool_delta(tool_call('ExecuteCode', {'code': read_final}, 'read'))],                     # judge inspects
        [{'content': 'Verdict ready.'}],                                                          # judge stops talking
        [submit(verdict)],                                                                        # nudged, it submits
    ]
    case = EvalCase('add_case', 'Add 2 and 3.', criteria=('correct sum',))

    with scripted_factory(responses, arithmetic_tools(invoked)) as (make_agent, api):
        grader = llm_judge('contract-model', shared_criteria=['units stated'], process_criteria=(), make_judge=make_agent)
        (record,) = run_evals(make_agent, [case], [grader])

    (score,) = record.scores
    assert (score.name, score.value) == ('judge', 0.5), 'one of two criteria met'
    assert '✓  correct sum' in score.reasoning and '✗  units stated' in score.reasoning, 'rubric text restored from numbers'
    assert 'Used add' in score.reasoning and '  - no units' in score.reasoning
    assert score.detail == ''

    judge_prompt = message_text(api.requests[2]['messages'][-1])
    assert '<rubric>\n1. correct sum\n2. units stated' in judge_prompt, 'case criteria first, shared after'
    assert '<task>\nAdd 2 and 3.' in judge_prompt
    assert 'tools.json' in judge_prompt, "the subject's tools reach the judge as a file"
    assert '"parameters"}}' in judge_prompt and '"add"' not in judge_prompt, 'tool schemas must not be inlined into the prompt'
    assert 'transcript.json' in judge_prompt and 'judge_add_case_' in judge_prompt, 'absolute path into the temp folder'
    assert 'The sum is 5' not in judge_prompt, 'the transcript must not be inlined into the prompt'

    # Plain text did not end the judge's run: it was nudged to submit, and the tool's schema is JudgeVerdict.
    assert 'SubmitResult' in message_text(api.requests[4]['messages'][-1])
    submit_tool = next(t['function'] for t in api.requests[4]['tools'] if t['function']['name'] == 'SubmitResult')
    assert submit_tool['parameters']['title'] == 'JudgeVerdict'
    assert len(api.requests) == 5, 'no extra structured-output call after the verdict'

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
    run = RunResult(final='5', messages=messages, tools=[], meta=EvalSink().meta, model='test-model')

    transcript = json.loads(write_transcript(tmp_path, run).read_text(encoding='utf-8'))

    assert [m['role'] for m in transcript] == ['user', 'tool']
    assert transcript[1]['content'] == '5'


def test_run_result_carries_full_schema_for_unloaded_deferred_tool() -> None:
    """A deferred tool the subject never loaded still reaches the judge with its full description and parameters."""
    define = {
        'name': 'Define',
        'description': 'Look up a word. Returns its dictionary definition.',
        'parameters': {'type': 'object', 'properties': {'word': {'type': 'string'}}, 'required': ['word']},
        'function': lambda word: None,
        'deferred': True,
    }

    with scripted_factory([[{'content': 'done'}]], [define]) as (make_agent, api):
        (record,) = run_evals(make_agent, [EvalCase('stub', 'Say done.')], [finished()])

    sent = {t['function']['name']: t['function'] for t in api.requests[0]['tools']}
    assert sent['Define']['description'].endswith('[deferred]'), 'the subject itself only saw the stub'

    recorded = {t['function']['name']: t['function'] for t in record.run.tools}
    assert recorded['Define']['description'] == define['description']
    assert recorded['Define']['parameters'] == define['parameters']


def test_write_tools_keeps_full_schemas(tmp_path: Path) -> None:
    """The tools file the judge reads is the subject's tool schemas, parameters included."""
    with scripted_factory([], arithmetic_tools([])) as (make_agent, _):
        agent = make_agent()

    run = RunResult(final='', messages=[], tools=list(agent.tools), meta=EvalSink().meta, model='test-model')

    tools = json.loads(write_tools(tmp_path, run).read_text(encoding='utf-8'))

    assert tools == agent.tools
    assert any(t['function']['name'] == 'add' and t['function']['parameters']['properties'] for t in tools)


def test_llm_judge_malformed_reply_scores_zero() -> None:
    """A verdict that fails the JudgeVerdict schema is sent back; a judge that never fixes it scores zero."""
    undecided = [[{'content': 'I cannot decide.'}]] * 4   # one more plain-text reply than the nudge limit
    responses = [[{'content': 'hello'}], [submit('{"reasoning": "unsure"}')], *undecided]

    with scripted_factory(responses) as (make_agent, api):
        grader = llm_judge('contract-model', shared_criteria=['any criterion'], process_criteria=(), make_judge=make_agent)
        (record,) = run_evals(make_agent, [EvalCase('greet', 'Say hello.')], [grader])

    (score,) = record.scores
    assert score.value == 0.0
    assert 'grader raised' in score.reasoning and 'without calling SubmitResult' in score.reasoning

    # The malformed submission came back to the judge as a schema error naming the missing field.
    rejection = api.requests[2]['messages'][-1]
    assert rejection['role'] == 'tool' and 'criteria' in message_text(rejection)


def test_llm_judge_appends_process_criteria_by_default() -> None:
    """With no process_criteria argument, the rubric is the case's criteria followed by PROCESS_CRITERIA."""
    responses = [[{'content': 'hello'}], [{'content': 'I cannot decide.'}]]

    with scripted_factory(responses) as (make_agent, api):
        grader = llm_judge('contract-model', make_judge=make_agent)
        run_evals(make_agent, [EvalCase('greet', 'Say hello.', criteria=('says hello',))], [grader])

    expected = '\n'.join(f'{i}. {c}' for i, c in enumerate(['says hello', *PROCESS_CRITERIA], 1))
    assert f'<rubric>\n{expected}\n</rubric>' in message_text(api.requests[1]['messages'][-1])


def test_llm_judge_rejects_skipped_criterion() -> None:
    """A verdict that skips a rubric number cannot inflate the score: it is a grader failure that scores zero."""
    verdict = '{"criteria": [{"number": 1, "met": true, "evidence": "says hello"}], "reasoning": "Fine.", "failures": []}'
    responses = [[{'content': 'hello'}], [submit(verdict)]]
    case = EvalCase('greet', 'Say hello.', criteria=('says hello', 'names the user'))

    with scripted_factory(responses) as (make_agent, _):
        (record,) = run_evals(make_agent, [case], [llm_judge('contract-model', process_criteria=(), make_judge=make_agent)])

    (score,) = record.scores
    assert score.value == 0.0, 'not 1.0 from averaging only the criterion it kept'
    assert 'grader raised' in score.reasoning and 'missing [2]' in score.reasoning


def test_llm_judge_skips_crashed_subject() -> None:
    """A crashed subject scores zero without a judge run: the only request made is the subject's failed one."""
    with scripted_factory([]) as (make_agent, api):
        grader = llm_judge('contract-model', shared_criteria=['any criterion'], process_criteria=(), make_judge=make_agent)
        (record,) = run_evals(make_agent, [EvalCase('crash', 'Anything.')], [grader])

    (score,) = record.scores
    assert (score.value, score.detail) == (0.0, 'subject crashed')
    assert len(api.requests) == 1


def test_report_shows_marks_and_judge_reasoning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The table shows ✓ / ✗ / decimals per grader; every judge score is followed by its reasoning; JSON lands in runs/."""
    verdict = ('{"criteria": [{"number": 1, "met": true, "evidence": "says 5"},'
               ' {"number": 2, "met": false, "evidence": "none"}],'
               ' "reasoning": "Answered 5 but gave no units.", "failures": ["no units"]}')
    responses = [
        [{'content': 'The sum is 5'}],                                                        # subject
        [tool_delta(tool_call('ExecuteCode', {'code': 'print(1)'}, 'read'))], [submit(verdict)],        # judge
    ]
    graders = [finished(), max_iterations(1)]

    with scripted_factory(responses) as (make_agent, _):
        graders.append(llm_judge('contract-model', process_criteria=(), make_judge=make_agent))
        records = run_evals(make_agent, [EvalCase('add_case', 'Add 2 and 3.', criteria=('correct', 'units'))], graders)

    print_report(records)
    out = capsys.readouterr().out

    assert 'case      finished  max_iterations  judge' in out
    assert 'add_case  ✓         ✓               0.50' in out
    assert 'mean      1.00      1.00            0.50' in out
    assert '── add_case · judge 0.50' in out
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
    assert report['scores'][2]['reasoning'].startswith('✓  correct')
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
        'model': 'contract-model',
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

