"""Deterministic loop contracts. Run: uv run --all-packages pytest tests/test_execution_loop.py"""
from __future__ import annotations

import json
import threading

from contract_support import (
    arithmetic_tools, message_text, scripted_agent, tool_call, tool_delta,
)


#     ================================
# --> Contract tests
#     ================================


def test_complete_tool_cycle() -> None:
    """Interleaved calls execute once and their paired results reach the next request."""
    invoked: list[tuple[str, int, int]] = []
    chunks = [
        {'tool_calls': [{'index': 1, 'id': 'product', 'type': 'function',
                         'function': {'name': 'multiply', 'arguments': '{"a":4,'}}]},
        {'tool_calls': [{'index': 0, 'id': 'sum', 'type': 'function',
                         'function': {'name': 'add', 'arguments': '{"a":2,'}}]},
        {'tool_calls': [{'index': 1, 'function': {'arguments': '"b":5}'}}]},
        {'tool_calls': [{'index': 0, 'function': {'arguments': '"b":3}'}}]},
    ]

    with scripted_agent([chunks, [{'content': '5 and 20'}]], arithmetic_tools(invoked)) as (agent, api, sink):
        assert agent.run('Calculate both.', sink=sink) == '5 and 20'
        assert invoked == [('add', 2, 3), ('multiply', 4, 5)], invoked
        assert len(api.requests) == 2
        history = api.requests[1]['messages']
        assert [m['role'] for m in history] == ['system', 'user', 'assistant', 'tool', 'tool']
        assert [c['id'] for c in history[2]['tool_calls']] == ['sum', 'product']
        assert [json.loads(c['function']['arguments']) for c in history[2]['tool_calls']] == [
            {'a': 2, 'b': 3}, {'a': 4, 'b': 5},
        ]
        assert [(m['tool_call_id'], message_text(m)) for m in history[3:]] == [('sum', '5'), ('product', '20')]
        assert message_text(agent.messages[-1]) == '5 and 20'
        assert sink.values('loop_end') == [('answer_ready', 2)]


def test_reasoning_exclusion() -> None:
    """Reasoning is observable live but absent from history and subsequent requests."""
    invoked: list[tuple[str, int, int]] = []
    reasoning = 'PRIVATE_REASONING_SENTINEL'
    first = [
        {'reasoning_content': reasoning}, {'content': 'Calculating.'},
        tool_delta(tool_call('add', {'a': 2, 'b': 3}, 'sum')),
    ]

    with scripted_agent([first, [{'content': '5'}], [{'content': 'Still 5'}]], arithmetic_tools(invoked)) as (agent, api, sink):
        assert agent.run('Add two and three.', sink=sink) == '5'
        assert agent.run('Repeat the result.', sink=sink) == 'Still 5'
        assert ''.join(sink.values('reasoning')) == reasoning
        assert sink.values('content') == ['Calculating.', '5', 'Still 5']
        assert len(api.requests) == 3
        assert reasoning not in json.dumps(agent.messages)
        assert reasoning not in json.dumps(api.requests)
        assert 'Calculating.' in json.dumps(api.requests[1]['messages'])
        assert invoked == [('add', 2, 3)]


def test_iteration_limit() -> None:
    """A model that keeps requesting tools cannot exceed the configured loop limit."""
    invoked: list[tuple[str, int, int]] = []
    responses = [[tool_delta(tool_call('add', {'a': i, 'b': 1}, f'call-{i}'))] for i in range(4)]

    with scripted_agent(responses, arithmetic_tools(invoked), max_iters=3) as (agent, api, sink):
        assert agent.run('Keep adding.', sink=sink) == ''
        assert len(api.requests) == 3, 'the fourth scripted response must remain unused'
        assert invoked == [('add', 0, 1), ('add', 1, 1), ('add', 2, 1)]
        assert sink.values('loop_end') == [('max_iterations', 3)]
        assert sink.values('turn_end') == ['']
        assert sink.values('interrupted') == []


def test_cancellation() -> None:
    """Cancelling a streamed call prevents execution and leaves history safe to resume."""
    for partial in (False, True):
        invoked: list[tuple[str, int, int]] = []
        cancel = threading.Event()
        call = tool_call('add', {'a': 2, 'b': 3}, 'cancelled-call')

        if partial:
            call.pop('id')

        response = [tool_delta(call), cancel.set, {'content': 'must not reach the answer'}]

        with scripted_agent([response, [{'content': 'Resumed'}]], arithmetic_tools(invoked)) as (agent, api, sink):
            assert agent.run('Calculate.', sink=sink, cancel_event=cancel) == ''
            assert invoked == []
            assert len(api.requests) == 1
            assert api.streams[0].closed, 'cancellation must close the active stream'
            assert sink.values('interrupted') == [None]
            assert sink.values('loop_end') == [('cancelled', 1)], sink.values('loop_end')
            assert sink.values('content') == []
            assert agent.run('Resume.', sink=sink) == 'Resumed'
            history = api.requests[1]['messages']
            calls = [c for m in history for c in m.get('tool_calls', [])]
            results = [m for m in history if m['role'] == 'tool']

            if partial:
                assert calls == [] and results == []
            else:
                assert [c['id'] for c in calls] == ['cancelled-call']
                assert [(m['tool_call_id'], message_text(m)) for m in results] == [('cancelled-call', '[interrupted]')]
