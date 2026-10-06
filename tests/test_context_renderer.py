"""Context renderer contracts. Run: uv run --all-packages pytest tests/test_context_renderer.py"""
from __future__ import annotations

import copy
import json
from typing import Any

from agent_harness import Agent
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult
from contract_support import arithmetic_tools, scripted_agent, tool_call, tool_delta


#     ================================
# --> Helper funcs
#     ================================


def cache_marked(message: dict[str, Any]) -> bool:
    """True when any content part of the message carries a cache_control marker."""
    content = message['content']

    return isinstance(content, list) and any('cache_control' in part for part in content)


#     ================================
# --> Contract tests
#     ================================


def test_system_message_keeps_anchor_and_deferred_protocol() -> None:
    """The system message is cached, carries the environment, and lists every deferred tool."""
    @agent_tool(deferred=True)
    def lookup(key: str) -> ToolResult:
        """Look up a key. Returns the stored value."""
        return ToolResult(key, status='ok')

    plain = Agent(model='contract-model', system='<role>Tester</role>')
    deferred = Agent(model='contract-model', tools=[lookup])

    for agent in (plain, deferred):
        assert len(agent.messages) == 1
        assert agent.messages[0]['role'] == 'system'
        assert cache_marked(agent.messages[0])
        assert '<environment>' in agent.messages[0]['content'][0]['text']

    plain_text = plain.messages[0]['content'][0]['text']
    deferred_text = deferred.messages[0]['content'][0]['text']

    assert plain_text.count('<role>Tester</role>') == 1
    # Plan is a deferred base tool, so every agent carries the protocol block.
    assert 'Deferred at session start: Plan.' in plain_text
    assert 'Deferred at session start: Plan, lookup.' in deferred_text

    plain.extend_system_prompt('<organization>Acme</organization>')

    assert len(plain.messages) == 1
    assert plain.messages[0]['content'][0]['text'].count('<organization>Acme</organization>') == 1


def test_render_is_independent_and_idempotent() -> None:
    """Rendering never mutates history and repeated renders are equal."""
    agent = Agent(model='contract-model')
    agent.messages += [
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': 'calling', 'tool_calls': [tool_call('add', {'a': 1, 'b': 2}, 'c1')]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': '3'},
    ]
    before = copy.deepcopy(agent.messages)

    first = agent.context_renderer.render(agent)
    second = agent.context_renderer.render(agent)

    assert agent.messages == before
    assert first == second
    assert [m['role'] for m in first] == ['system', 'user', 'assistant', 'tool']
    assert first[2]['tool_calls'] == before[2]['tool_calls']
    assert [cache_marked(m) for m in first] == [True, False, False, True]


def test_multipart_content_survives_with_one_rolling_marker() -> None:
    """Every part of a multipart message is kept; only the latest eligible text is marked."""
    agent = Agent(model='contract-model')
    multipart = [
        {'type': 'text', 'text': 'first', 'cache_control': {'type': 'ephemeral'}},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}},
        {'type': 'text', 'text': 'second'},
    ]
    agent.messages += [
        {'role': 'user', 'content': 'hi'},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': copy.deepcopy(multipart)},
        {'role': 'assistant', 'content': 'done'},
    ]

    request = agent.context_renderer.render(agent)

    assert [p.get('text') for p in request[2]['content']] == ['first', None, 'second']
    assert not cache_marked(request[2])
    assert cache_marked(request[3])
    assert sum(cache_marked(m) for m in request) == 2

    agent.messages.pop()
    request = agent.context_renderer.render(agent)

    assert [('cache_control' in p) for p in request[2]['content']] == [False, False, True]
    assert agent.messages[2]['content'] == multipart


def test_loop_sends_rendered_requests_and_keeps_canonical_history() -> None:
    """Over a real tool cycle, requests carry the markers while agent.messages stays plain."""
    invoked: list[tuple[str, int, int]] = []
    first = [tool_delta(tool_call('add', {'a': 2, 'b': 3}, 'sum'))]

    with scripted_agent([first, [{'content': '5'}], [{'content': 'again'}]], arithmetic_tools(invoked)) as (agent, api, sink):
        agent.run('Add.', sink=sink)
        agent.run('Repeat.', sink=sink)

        first_request, second_request, third_request = (r['messages'] for r in api.requests)

        # The first request of each turn ends on the user task, so the task itself is marked.
        assert sum(cache_marked(m) for m in first_request) == 2
        assert cache_marked(first_request[-1]) and first_request[-1]['role'] == 'user'
        assert sum(cache_marked(m) for m in second_request) == 2
        assert cache_marked(second_request[-1]) and second_request[-1]['role'] == 'tool'
        assert sum(cache_marked(m) for m in third_request) == 2
        assert cache_marked(third_request[-1]) and third_request[-1]['role'] == 'user'
        assert all(isinstance(m['content'], str) for m in agent.messages[1:])
        assert 'cache_control' not in json.dumps(agent.messages[1:])


def test_dynamic_context_appears_only_when_a_provider_returns_text() -> None:
    """Providers render in order into one trailing, uncached, unstored block; None is skipped."""
    quotes: list[str] = []

    def quotes_context() -> str | None:
        return '<quotes>\n' + '\n'.join(quotes) + '\n</quotes>' if quotes else None

    agent = Agent(
        model='contract-model',
        dynamic_context_providers=[quotes_context, lambda: None, lambda: '<inbox>2 unread</inbox>'],
    )
    agent.messages.append({'role': 'user', 'content': 'hi'})

    assert agent.context_renderer.render(agent)[-1]['content'] == '<dynamic_context>\n<inbox>2 unread</inbox>\n</dynamic_context>'

    quotes.append('AAPL 332.70')
    before = copy.deepcopy(agent.messages)

    request = agent.context_renderer.render(agent)

    assert len(request) == 3
    assert request[-1] == {
        'role': 'user',
        'content': '<dynamic_context>\n<quotes>\nAAPL 332.70\n</quotes>\n\n<inbox>2 unread</inbox>\n</dynamic_context>',
    }
    assert cache_marked(request[-2])
    assert agent.messages == before


def test_no_providers_means_no_block() -> None:
    """A default agent renders history only — no dynamic block at all."""
    agent = Agent(model='contract-model')
    agent.messages.append({'role': 'user', 'content': 'hi'})

    request = agent.context_renderer.render(agent)

    assert [m['role'] for m in request] == ['system', 'user']


def test_dynamic_context_never_accumulates_across_iterations() -> None:
    """Over a real tool loop, each request carries exactly one fresh block and history carries none."""
    invoked: list[tuple[str, int, int]] = []
    readings = iter(range(1, 100))
    first = [tool_delta(tool_call('add', {'a': 2, 'b': 3}, 'sum'))]

    with scripted_agent([first, [{'content': '5'}]], arithmetic_tools(invoked)) as (agent, api, sink):
        agent.context_renderer.dynamic_providers.append(lambda: f'<reading>{next(readings)}</reading>')
        agent.run('Add.', sink=sink)

        blocks = [request['messages'][-1]['content'] for request in api.requests]

        assert blocks == [
            '<dynamic_context>\n<reading>1</reading>\n</dynamic_context>',
            '<dynamic_context>\n<reading>2</reading>\n</dynamic_context>',
        ]
        assert all(json.dumps(r['messages']).count('<dynamic_context>') == 1 for r in api.requests)
        assert api.requests[1]['messages'][-2]['role'] == 'tool'
        assert cache_marked(api.requests[1]['messages'][-2])
        assert '<dynamic_context>' not in json.dumps(agent.messages)
