"""FailureStreaks tracker and tool-blocking contracts. Run: uv run --all-packages pytest tests/test_failure_streak.py"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel

from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.failure_streak import FailureStreaks
from agent_harness.tooling.result import ToolResult
from contract_support import arithmetic_tools, message_text, scripted_agent, tool_call, tool_delta


ORDER_ERROR = 'error: invalid request: Both qty and notional can not be set.'


#     ================================
# --> Helper funcs
#     ================================


def fail(streaks: FailureStreaks, name: str, times: int) -> list[bool]:
    """Record `times` error results for `name`; return each call's 'now blocked' flag."""
    return [streaks.record(name, 'error', ORDER_ERROR) for _ in range(times)]


def order_tool(outcomes: list[bool], ran: list[str], name: str = 'SubmitOrder',
               safe_parallel: bool = False) -> Callable:
    """A broker-like tool that pops its next outcome (True = filled), defaulting to failure."""
    @agent_tool(name=name, safe_parallel=safe_parallel)
    def submit_order(client_order_id: str) -> ToolResult:
        """Submit an order."""
        ran.append(client_order_id)

        if outcomes and outcomes.pop(0):
            return ToolResult('filled', status='ok')

        return ToolResult(ORDER_ERROR, status='error')

    return submit_order


def calls(count: int, name: str = 'SubmitOrder', prefix: str = 'o') -> list[list[dict[str, Any]]]:
    """One model response per call, each with a fresh client_order_id (the retry pattern that beat the prompt rule)."""
    return [[tool_delta(tool_call(name, {'client_order_id': f'{prefix}{i}'}, f'{prefix}{i}'))] for i in range(count)]


def tool_results(messages: Sequence[dict[str, Any]]) -> list[str]:
    """The text of every tool-result message, in order."""
    return [message_text(m) for m in messages if m['role'] == 'tool']


def test_blocks_on_the_fifth_consecutive_failure() -> None:
    """The streak blocks exactly on failure #5, and the deny message carries the last error."""
    streaks = FailureStreaks()

    assert fail(streaks, 'SubmitOrder', 5) == [False, False, False, False, True]

    reason = streaks.block_reason('SubmitOrder')

    assert reason is not None
    assert 'failed 5 times in a row' in reason
    assert ORDER_ERROR in reason
    assert reason.startswith('Blocked by the agent harness: SubmitOrder')
    assert 'Use a different approach if one exists' in reason


def test_success_resets_and_non_errors_do_not_count() -> None:
    """An ok result resets the streak; denied and interrupted results leave it alone."""
    streaks = FailureStreaks()

    fail(streaks, 'SubmitOrder', 2)
    streaks.record('SubmitOrder', 'ok', 'filled')
    fail(streaks, 'SubmitOrder', 4)
    streaks.record('SubmitOrder', 'denied', 'denied: gate')
    streaks.record('SubmitOrder', 'interrupted', '[interrupted]')

    assert streaks.block_reason('SubmitOrder') is None


def test_streaks_are_per_tool() -> None:
    """One tool's failures never block another tool."""
    streaks = FailureStreaks()

    fail(streaks, 'SubmitOrder', 5)

    assert streaks.block_reason('SubmitOrder') is not None
    assert streaks.block_reason('GetQuote') is None


def test_report_appears_after_five_blocked_calls() -> None:
    """`report` stays None until the blocked tool is called 5 more times."""
    streaks = FailureStreaks()

    fail(streaks, 'SubmitOrder', 5)

    for _ in range(4):
        streaks.block_reason('SubmitOrder')

        assert streaks.report is None

    streaks.block_reason('SubmitOrder')
    report = streaks.report

    assert report is not None
    assert report.startswith('Stopped by the agent harness: SubmitOrder failed 5 times')
    assert ORDER_ERROR in report


def test_reset_clears_everything() -> None:
    """After reset, the blocked tool runs again and the report is gone."""
    streaks = FailureStreaks()

    fail(streaks, 'SubmitOrder', 5)

    for _ in range(5):
        streaks.block_reason('SubmitOrder')

    streaks.reset()

    assert streaks.report is None
    assert streaks.block_reason('SubmitOrder') is None


def test_parallel_records_count_exactly() -> None:
    """Concurrent records from pool threads block exactly once, with no lost updates."""
    streaks = FailureStreaks()

    with ThreadPoolExecutor(max_workers=8) as pool:
        flags = list(pool.map(lambda _: streaks.record('SubmitOrder', 'error', ORDER_ERROR), range(100)))

    assert flags.count(True) == 1
    assert streaks.block_reason('SubmitOrder') is not None


#     ================================
# --> Contract tests (real loop + handler)
#     ================================


def test_tool_is_blocked_after_five_failures() -> None:
    """8 failing calls: the body runs 5 times, calls 6-8 are denied, and the block is surfaced once."""
    ran: list[str] = []
    done = [[{'content': 'Reporting: SubmitOrder keeps failing.'}]]

    with scripted_agent(calls(8) + done, [order_tool([], ran)]) as (agent, api, sink):
        agent.run('Place the stop order.', sink=sink)

        results = tool_results(agent.messages)

        assert ran == ['o0', 'o1', 'o2', 'o3', 'o4']
        assert results[:5] == [ORDER_ERROR] * 5
        assert all(r.startswith('denied: Blocked by the agent harness: SubmitOrder failed 5 times in a row') for r in results[5:])
        assert len(results) == 8
        assert [outcome.status for _, outcome in sink.values('tool_end')][5:] == ['denied'] * 3
        assert len(sink.values('error')) == 1
        assert sink.values('error')[0].startswith('tool.blocked SubmitOrder after 5 consecutive failures')


def test_success_resets_streak_in_a_run() -> None:
    """fail, fail, ok, then 4 fails: every call still runs."""
    ran: list[str] = []
    done = [[{'content': 'done'}]]

    with scripted_agent(calls(7) + done, [order_tool([False, False, True], ran)]) as (agent, api, sink):
        agent.run('Place orders.', sink=sink)

        assert len(ran) == 7
        assert sink.values('error') == []


def test_blocking_one_tool_leaves_others_running() -> None:
    """SubmitOrder is blocked; CancelOrder still runs."""
    submitted: list[str] = []
    cancelled: list[str] = []
    tools = [order_tool([], submitted), order_tool([True], cancelled, name='CancelOrder')]
    responses = calls(6) + calls(1, name='CancelOrder', prefix='c') + [[{'content': 'done'}]]

    with scripted_agent(responses, tools) as (agent, api, sink):
        agent.run('Place, then cancel.', sink=sink)

        assert len(submitted) == 5
        assert cancelled == ['c0']
        assert tool_results(agent.messages)[-1] == 'filled'


def test_new_run_starts_with_fresh_streaks() -> None:
    """A tool blocked in run 1 runs again in run 2."""
    ran: list[str] = []
    responses = calls(6) + [[{'content': 'blocked'}]] + calls(1, prefix='r') + [[{'content': 'done'}]]

    with scripted_agent(responses, [order_tool([], ran)]) as (agent, api, sink):
        agent.run('First turn.', sink=sink)
        agent.run('Second turn.', sink=sink)

        assert ran == ['o0', 'o1', 'o2', 'o3', 'o4', 'r0']


def test_parallel_failures_block_once() -> None:
    """A safe-parallel batch of 6 failures blocks the tool exactly once; the next call is denied."""
    ran: list[str] = []
    batch = [tool_delta(*(tool_call('SubmitOrder', {'client_order_id': f'p{i}'}, f'p{i}') for i in range(6)))]
    responses = [batch] + calls(1, prefix='n') + [[{'content': 'done'}]]

    with scripted_agent(responses, [order_tool([], ran, safe_parallel=True)]) as (agent, api, sink):
        agent.run('Place all orders.', sink=sink)

        assert 'n0' not in ran
        assert len(sink.values('error')) == 1
        assert tool_results(agent.messages)[-1].startswith('denied: Blocked by the agent harness: SubmitOrder failed 5 times')


def test_retrying_a_blocked_tool_ends_the_run_with_a_report() -> None:
    """5 failures + 5 denied calls end the run long before max_iters, returning the report."""
    ran: list[str] = []

    # 20 scripted calls: the loop must stop after the 10th, so the rest are never requested
    with scripted_agent(calls(20), [order_tool([], ran)], max_iters=50) as (agent, api, sink):
        result = agent.run('Place the stop order.', sink=sink)

        assert len(ran) == 5
        assert len(api.requests) == 10
        assert sink.values('loop_end') == [('tool_failure_loop', 10)]
        assert isinstance(result, str)
        assert result.startswith('Stopped by the agent harness: SubmitOrder failed 5 times in a row')
        assert ORDER_ERROR in result
        assert sink.values('turn_end') == [result]


def test_changing_course_after_a_block_does_not_stop_the_run() -> None:
    """Blocked once, then the model switches tools and answers: the run ends normally."""
    submitted: list[str] = []
    cancelled: list[str] = []
    tools = [order_tool([], submitted), order_tool([True], cancelled, name='CancelOrder')]
    responses = calls(6) + calls(1, name='CancelOrder', prefix='c') + [[{'content': 'Cancelled instead.'}]]

    with scripted_agent(responses, tools) as (agent, api, sink):
        assert agent.run('Place, then cancel.', sink=sink) == 'Cancelled instead.'
        assert sink.values('loop_end') == [('answer_ready', 8)]


def test_untracked_tools_are_never_blocked() -> None:
    """ExecuteCode and SubmitResult errors are normal iteration: no streak, no block."""
    streaks = FailureStreaks()

    for name in ('ExecuteCode', 'SubmitResult'):
        assert fail(streaks, name, 20) == [False] * 20
        assert streaks.block_reason(name) is None


def test_verifier_rejections_never_block_submit_result() -> None:
    """5 rejected submissions, with successful work between them, still let the correct one through."""
    class Sum(BaseModel):
        total: int

    responses: list[list[dict[str, Any]]] = []

    for i in range(5):
        responses += [[tool_delta(tool_call('add', {'a': 2, 'b': 3}, f'w{i}'))],
                      [tool_delta(tool_call('SubmitResult', {'total': 99}, f's{i}'))]]

    responses += [[tool_delta(tool_call('SubmitResult', {'total': 5}, 'right'))]]

    with scripted_agent(responses, arithmetic_tools([])) as (agent, api, sink):
        agent.output_model = Sum
        agent.verifier = lambda r: None if r.total == 5 else f'total {r.total} is wrong'

        assert agent.run('Add 2 and 3.', sink=sink) == Sum(total=5)
        assert sink.values('error') == []
