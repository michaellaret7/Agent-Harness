"""Deterministic dispatch contracts. Run: uv run --all-packages pytest tests/test_tool_handler.py"""
from __future__ import annotations

import threading
from pathlib import Path

from agent_harness import Agent
from agent_harness.gates import GateContext, GateVerdict
from agent_harness.sinks.base import ToolOutcome
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult
from contract_support import RecordingSink, tool_call


#     ================================
# --> Helper funcs
#     ================================


def failing_gate(ctx: GateContext) -> GateVerdict:
    """Simulate a broken guard before any tool side effect."""
    raise RuntimeError('intentional gate failure')


def check_blocked_write(path: Path, raises: bool) -> None:
    """Exercise denial or guard failure against a real file-writing tool."""
    subsequent: list[str] = []
    sink = RecordingSink()

    @agent_tool
    def write_file(text: str) -> ToolResult:
        """Write the supplied text inside this test's temporary directory."""
        path.write_text(text, encoding='utf-8')

        return ToolResult('written', status='ok')

    def later_gate(ctx: GateContext) -> GateVerdict:
        subsequent.append(ctx.tool_name)

        return GateVerdict.allow()

    agent = Agent(tools=[write_file])
    agent.add_gate(failing_gate if raises else lambda ctx: GateVerdict.deny('blocked'), tool='write_file')
    agent.add_gate(later_gate)
    messages = agent.tool_handler.execute([tool_call('write_file', {'text': 'forbidden'}, 'write')], sink, threading.Event())

    assert not path.exists(), 'blocked tool caused a filesystem side effect'
    assert subsequent == [], 'a blocking gate must stop the remaining gate chain'
    assert messages[0]['tool_call_id'] == 'write'
    assert messages[0]['content'].startswith('denied:')
    assert sink.values('tool_end')[0][1].status == 'denied'


def check_rewritten_write(path: Path) -> None:
    """Rewritten arguments reach subsequent guards and the actual tool."""
    seen: list[dict] = []

    @agent_tool
    def write_file(text: str) -> ToolResult:
        """Write the resolved argument to a real file."""
        path.write_text(text, encoding='utf-8')

        return ToolResult(text, status='ok')

    def observe(ctx: GateContext) -> GateVerdict:
        seen.append(dict(ctx.args))

        return GateVerdict.allow()

    agent = Agent(tools=[write_file])
    agent.add_gate(lambda ctx: GateVerdict.rewrite({'text': 'approved'}))
    agent.add_gate(observe)
    messages = agent.tool_handler.execute([tool_call('write_file', {'text': 'original'}, 'write')], RecordingSink(), threading.Event())

    assert seen == [{'text': 'approved'}]
    assert path.read_text(encoding='utf-8') == 'approved'
    assert messages[0]['content'] == 'approved'


class OrderedSink(RecordingSink):
    """Release a waiting tool only after another tool's outcome is recorded."""

    def __init__(self, fast_ended: threading.Event) -> None:
        super().__init__()
        self.fast_ended = fast_ended

    def on_tool_end(self, tool_call_id: str, outcome: ToolOutcome) -> None:
        """Record completion before releasing the slow worker."""
        super().on_tool_end(tool_call_id, outcome)

        if tool_call_id == 'fast':
            self.fast_ended.set()


#     ================================
# --> Contract tests
#     ================================


def test_gate_enforcement(tmp_path: Path) -> None:
    """Denials and gate failures block side effects; rewrites control actual arguments."""
    path = tmp_path / 'output.txt'

    check_blocked_write(path, raises=False)
    check_blocked_write(path, raises=True)
    check_rewritten_write(path)


def test_tool_error_handling() -> None:
    """Bad calls become paired errors while subsequent valid calls still execute."""
    invoked: list[str] = []
    sink = RecordingSink()

    @agent_tool
    def action(mode: str) -> ToolResult | str:
        """Produce a real success, exception, or invalid result for contract checking."""
        invoked.append(mode)

        if mode == 'raise':
            raise RuntimeError('intentional tool failure')

        if mode == 'invalid':
            return 'not a ToolResult'

        return ToolResult('error: this is successful payload text', status='ok')

    agent = Agent(tools=[action])
    malformed = tool_call('action', {}, 'bad-json')
    malformed['function']['arguments'] = '{'
    calls = [malformed, tool_call('missing', {}, 'unknown'),
             tool_call('action', {'mode': 'raise'}, 'exception'),
             tool_call('action', {'mode': 'invalid'}, 'invalid-return'),
             tool_call('action', {'mode': 'valid'}, 'success')]
    messages = agent.tool_handler.execute(calls, sink, threading.Event())
    outcomes = sink.values('tool_end')

    assert [m['tool_call_id'] for m in messages] == [c['id'] for c in calls]
    assert [call_id for call_id, _ in outcomes] == [c['id'] for c in calls]
    assert [outcome.status for _, outcome in outcomes] == ['error'] * 4 + ['ok']
    assert all(m['content'].startswith('error:') for m in messages[:4])
    assert invoked == ['raise', 'invalid', 'valid']
    assert messages[-1]['content'] == 'error: this is successful payload text'
    assert [event[0] for event in sink.values('tool_start')] == [c['id'] for c in calls]


def test_parallel_boundaries() -> None:
    """Parallel calls overlap, serial calls form boundaries, and result order stays stable."""
    started = threading.Barrier(2, timeout=5)
    fast_ended = threading.Event()
    serial_done = threading.Event()
    sink = OrderedSink(fast_ended)

    @agent_tool(safe_parallel=True)
    def parallel(label: str) -> ToolResult:
        """Force overlap and reverse completion order without timed sleeps."""
        started.wait()

        if label == 'slow':
            assert fast_ended.wait(5), 'fast tool must finish before slow tool'

        return ToolResult(label, status='ok')

    @agent_tool
    def serial() -> ToolResult:
        """Require the preceding batch to finish before permitting the following one."""
        assert {key for key, _ in sink.values('tool_end')} == {'slow', 'fast'}
        serial_done.set()

        return ToolResult('serial', status='ok')

    @agent_tool(safe_parallel=True)
    def tail() -> ToolResult:
        """Verify that the following parallel batch cannot cross the serial boundary."""
        assert serial_done.is_set(), 'tail started before the serial tool'

        return ToolResult('tail', status='ok')

    agent = Agent(tools=[parallel, serial, tail])
    calls = [tool_call('parallel', {'label': 'slow'}, 'slow'), tool_call('parallel', {'label': 'fast'}, 'fast'),
             tool_call('serial', {}, 'serial'), tool_call('tail', {}, 'tail')]
    messages = agent.tool_handler.execute(calls, sink, threading.Event())

    assert [key for key, _ in sink.values('tool_end')] == ['fast', 'slow', 'serial', 'tail']
    assert all(outcome.status == 'ok' for _, outcome in sink.values('tool_end'))
    assert [(m['tool_call_id'], m['content']) for m in messages] == [(name, name) for name in ('slow', 'fast', 'serial', 'tail')]
