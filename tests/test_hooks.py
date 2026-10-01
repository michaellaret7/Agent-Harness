"""Deterministic hook contracts. Run: uv run --all-packages pytest tests/test_hooks.py"""
from __future__ import annotations

import threading

import pytest

from agent_harness.hooks import HookContext
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult
from contract_support import arithmetic_tools, scripted_agent, tool_call, tool_delta


#     ================================
# --> Contract tests
#     ================================


def test_hook_failure_isolation() -> None:
    """A raising observer does not stop later observers or completion of the turn."""
    observed: list[str] = []

    def broken(ctx: HookContext) -> None:
        observed.append('broken')
        raise RuntimeError('intentional observer failure')

    def healthy(ctx: HookContext) -> None:
        observed.append(ctx.event)

    with scripted_agent([[{'content': 'Finished'}]]) as (agent, api, sink):
        agent.add_hook('turn_start', broken)
        agent.add_hook('turn_start', healthy)
        agent.add_hook('turn_end', healthy)

        assert agent.run('Answer.', sink=sink) == 'Finished'
        assert observed == ['broken', 'turn_start', 'turn_end'], observed
        assert len(api.requests) == 1
        assert sink.values('turn_end') == ['Finished']


def test_hook_tool_filtering() -> None:
    """Tool filters select only matching events and reject unknown tool names."""
    invoked: list[tuple[str, int, int]] = []
    observed: list[tuple[str, str | None, str | None]] = []
    response = tool_delta(
        tool_call('add', {'a': 2, 'b': 3}, 'sum'),
        tool_call('multiply', {'a': 4, 'b': 5}, 'product'),
    )

    def record(ctx: HookContext) -> None:
        observed.append((ctx.event, ctx.tool_name, ctx.tool_call_id))

    with scripted_agent([[response], [{'content': 'Done'}]], arithmetic_tools(invoked)) as (agent, api, sink):
        agent.add_hook('tool_start', record, tool='add')
        agent.add_hook('tool_end', record, tool=['add'])

        with pytest.raises(ValueError, match='unknown-tool'):
            agent.add_hook('tool_end', record, tool='unknown-tool')

        assert agent.run('Calculate.', sink=sink) == 'Done'
        assert observed == [('tool_start', 'add', 'sum'), ('tool_end', 'add', 'sum')]
        assert invoked == [('add', 2, 3), ('multiply', 4, 5)]


def test_hook_context_and_ordering() -> None:
    """Parallel calls keep their own hook context when their completion order reverses."""
    started = threading.Barrier(2, timeout=5)
    fast_observed = threading.Event()
    lock = threading.Lock()
    observed: list[HookContext] = []

    @agent_tool(safe_parallel=True)
    def worker(label: str) -> ToolResult:
        """Coordinate two calls so the fast call's end hook must arrive first."""
        started.wait()

        if label == 'slow':
            assert fast_observed.wait(5), 'fast call did not reach its end hook'

        return ToolResult(label.upper(), status='ok')

    def record(ctx: HookContext) -> None:
        with lock:
            observed.append(ctx)

        if ctx.event == 'tool_end' and ctx.tool_call_id == 'fast-id':
            fast_observed.set()

    response = tool_delta(tool_call('worker', {'label': 'slow'}, 'slow-id'),
                          tool_call('worker', {'label': 'fast'}, 'fast-id'))

    with scripted_agent([[response], [{'content': 'Done'}]], [worker]) as (agent, api, sink):
        agent.add_hook('tool_start', record)
        agent.add_hook('tool_end', record)
        assert agent.run('Run both.', sink=sink) == 'Done'
        assert [ctx.tool_call_id for ctx in observed if ctx.event == 'tool_end'] == ['fast-id', 'slow-id']

        for label in ('slow', 'fast'):
            events = [ctx for ctx in observed if ctx.tool_call_id == f'{label}-id']
            assert [ctx.event for ctx in events] == ['tool_start', 'tool_end']
            assert all(ctx.agent is agent and ctx.tool_name == 'worker' for ctx in events)
            assert all(ctx.args == {'label': label} for ctx in events)
            assert events[0].outcome is None
            assert events[1].outcome is not None
            assert (events[1].outcome.payload, events[1].outcome.status) == (label.upper(), 'ok')
