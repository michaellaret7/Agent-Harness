"""End-to-end ToolHandler verification — one command, everything visible.

Runs three groups of assertions against `ToolHandler.execute()`:
  1. single-tool dispatch (happy path + every documented edge case)
  2. pure-parallel batch (order preservation, real parallelism, errors)
  3. mixed batch — 3 parallel + 1 serial barrier + 3 parallel (the boundary case)

After every assertion passes, replays the mixed batch one more time with a
live `StdoutSink` and prints an ASCII Gantt chart so the chunked dispatch
can be inspected by eye.

Run: `uv run python -m agent_harness.tests.tool_handler.test_tool_handler`
"""
from __future__ import annotations

import json
import threading
import time

from agent_harness.sinks import StdoutSink
from agent_harness.tests.tool_handler.fixtures import (
    ExecutionLog,
    RecordingSink,
    make_cancelling_tool,
    make_error_tool,
    make_fast_tool,
    make_handler,
    make_logging_tool,
    make_slow_tool,
    tool_call,
)

CHUNK_1 = ['p1', 'p2', 'p3']
BARRIER = 'b1'
CHUNK_2 = ['p5', 'p6', 'p7']

PARALLEL_SLEEP = 0.20
BARRIER_SLEEP = 0.10
CHART_WIDTH = 60

#     ================================
# --> Helper funcs
#     ================================


def _run_single(name: str, tools: dict, args_json: str = '{}', cancelled: bool = False) -> tuple:
    """Drive the handler with one call. Returns (messages, sink)."""
    handler = make_handler(tools)
    sink = RecordingSink()
    cancel = threading.Event()

    if cancelled:
        cancel.set()

    calls = [tool_call('call-1', name, args_json)]

    messages = handler.execute(calls, sink, cancel)  # type: ignore[arg-type]

    return messages, sink


def _build_mixed_batch() -> tuple[dict, list[dict], ExecutionLog]:
    """Build the 3-parallel / 1-serial / 3-parallel batch shared by tests and demo."""
    log = ExecutionLog()

    tools = {
        'Par':    make_logging_tool('Par',    safe_parallel=True,  sleep_s=PARALLEL_SLEEP, log=log),
        'Serial': make_logging_tool('Serial', safe_parallel=False, sleep_s=BARRIER_SLEEP,  log=log),
    }

    submitted = CHUNK_1 + [BARRIER] + CHUNK_2

    calls = [
        tool_call(
            tid,
            'Serial' if tid == BARRIER else 'Par',
            json.dumps({'tid': tid}),
        )
        for tid in submitted
    ]

    return tools, calls, log


#     ================================
# --> Single-tool dispatch
#     ================================


def test_happy_path() -> None:
    """One parallel-safe tool returns its result and emits start/end events."""
    tool = make_fast_tool('Ping', safe_parallel=True)

    messages, sink = _run_single('Ping', {'Ping': tool})

    assert len(messages) == 1
    assert messages[0]['role'] == 'tool'
    assert messages[0]['tool_call_id'] == 'call-1'
    assert messages[0]['content'] == 'ok-Ping'

    assert sink.starts() == ['call-1']
    assert sink.ends() == ['call-1']


def test_tool_exception_surfaces_as_error_string() -> None:
    """A raising tool becomes 'error: RuntimeError: boom' — no exception escapes."""
    tool = make_error_tool('Boom', safe_parallel=True)

    messages, sink = _run_single('Boom', {'Boom': tool})

    content = messages[0]['content']

    assert content.startswith('error: RuntimeError'), content
    assert 'boom' in content
    assert sink.ends() == ['call-1'], 'on_tool_end must still fire after a tool raises'


def test_unknown_tool_name() -> None:
    """An unregistered tool name produces 'error: unknown tool ...'."""
    messages, _ = _run_single('Nope', tools={})

    assert messages[0]['content'] == "error: unknown tool 'Nope'"


def test_bad_arguments_json() -> None:
    """Malformed JSON in arguments produces 'error: bad arguments JSON: ...'."""
    tool = make_fast_tool('Ping', safe_parallel=True)

    messages, _ = _run_single('Ping', {'Ping': tool}, args_json='{not json')

    assert messages[0]['content'].startswith('error: bad arguments JSON:')


def test_cancel_event_short_circuits() -> None:
    """A pre-set cancel_event yields '[interrupted]' without invoking the tool."""
    tool = make_error_tool('NeverCalled', safe_parallel=False)

    messages, sink = _run_single(
        'NeverCalled',
        {'NeverCalled': tool},
        cancelled=True,
    )

    assert messages[0]['content'] == '[interrupted]'
    assert sink.starts() == ['call-1'], 'start fires before the cancel check'
    assert sink.ends() == ['call-1']


#     ================================
# --> Pure-parallel batch
#     ================================


def test_results_preserve_submission_order() -> None:
    """Inverted sleep durations: completion order is reversed; output order must not be."""
    sleeps = [0.30, 0.24, 0.18, 0.12, 0.06]
    tools = {
        f'Sleeper{i}': make_slow_tool(f'Sleeper{i}', safe_parallel=True, sleep_s=s)
        for i, s in enumerate(sleeps)
    }
    handler = make_handler(tools)

    calls = [tool_call(f'p{i}', f'Sleeper{i}') for i in range(5)]
    messages = handler.execute(calls, RecordingSink(), threading.Event())  # type: ignore[arg-type]

    assert [m['tool_call_id'] for m in messages] == ['p0', 'p1', 'p2', 'p3', 'p4']
    assert [m['content'] for m in messages] == [f'slow-Sleeper{i}' for i in range(5)]


def test_concurrent_start_windows_overlap() -> None:
    """Stronger than wall-clock: every tool's start_ts precedes the earliest end_ts."""
    log = ExecutionLog()
    sleep_s = 0.15
    tools = {
        f'Log{i}': make_logging_tool(f'Log{i}', safe_parallel=True, sleep_s=sleep_s, log=log)
        for i in range(4)
    }
    handler = make_handler(tools)

    calls = [
        tool_call(f'p{i}', f'Log{i}', json.dumps({'tid': f'p{i}'}))
        for i in range(4)
    ]

    handler.execute(calls, RecordingSink(), threading.Event())  # type: ignore[arg-type]

    entries = log.entries()

    assert len(entries) == 4

    earliest_end = min(end for _, _, end in entries)
    latest_start = max(start for _, start, _ in entries)

    assert latest_start < earliest_end, (
        f'expected overlapping windows: latest_start={latest_start:.4f}, '
        f'earliest_end={earliest_end:.4f}'
    )


def test_one_tool_error_does_not_break_the_batch() -> None:
    """A raising tool produces an `error:` message; sibling results still arrive."""
    tools = {
        'GoodA': make_slow_tool('GoodA', safe_parallel=True, sleep_s=0.05),
        'Bad':   make_error_tool('Bad',  safe_parallel=True),
        'GoodB': make_slow_tool('GoodB', safe_parallel=True, sleep_s=0.05),
    }
    handler = make_handler(tools)

    calls = [
        tool_call('a', 'GoodA'),
        tool_call('b', 'Bad'),
        tool_call('c', 'GoodB'),
    ]
    messages = handler.execute(calls, RecordingSink(), threading.Event())  # type: ignore[arg-type]

    assert [m['tool_call_id'] for m in messages] == ['a', 'b', 'c']
    assert messages[0]['content'] == 'slow-GoodA'
    assert messages[1]['content'].startswith('error: RuntimeError')
    assert messages[2]['content'] == 'slow-GoodB'


def test_sink_sees_every_parallel_tool() -> None:
    """on_tool_start and on_tool_end fire once per tool, even when concurrent."""
    tools = {
        f'Sink{i}': make_slow_tool(f'Sink{i}', safe_parallel=True, sleep_s=0.05)
        for i in range(6)
    }
    handler = make_handler(tools)
    sink = RecordingSink()

    calls = [tool_call(f'p{i}', f'Sink{i}') for i in range(6)]
    handler.execute(calls, sink, threading.Event())  # type: ignore[arg-type]

    submitted = {f'p{i}' for i in range(6)}

    assert set(sink.starts()) == submitted
    assert set(sink.ends()) == submitted


#     ================================
# --> Mixed batch (parallel + serial barrier + parallel)
#     ================================


def test_mixed_batch_preserves_submission_order() -> None:
    """Across both parallel chunks and the barrier, output order must equal input order."""
    tools, calls, _ = _build_mixed_batch()
    handler = make_handler(tools)

    messages = handler.execute(calls, RecordingSink(), threading.Event())  # type: ignore[arg-type]

    expected_ids = CHUNK_1 + [BARRIER] + CHUNK_2
    actual_ids = [m['tool_call_id'] for m in messages]

    assert actual_ids == expected_ids, actual_ids

    for m in messages:
        tid = m['tool_call_id']
        expected_prefix = 'log-Serial-' if tid == BARRIER else 'log-Par-'

        assert m['content'] == f'{expected_prefix}{tid}'


def test_mixed_batch_each_chunk_overlaps_internally() -> None:
    """Within a chunk, every tool starts before any other in the same chunk ends."""
    tools, calls, log = _build_mixed_batch()
    handler = make_handler(tools)

    handler.execute(calls, RecordingSink(), threading.Event())  # type: ignore[arg-type]

    for label, chunk_ids in (('chunk-1', CHUNK_1), ('chunk-2', CHUNK_2)):
        windows = [log.window(tid) for tid in chunk_ids]

        latest_start = max(start for start, _ in windows)
        earliest_end = min(end for _, end in windows)

        assert latest_start < earliest_end, f'{label} did not overlap'


def test_mixed_batch_barrier_separates_the_chunks() -> None:
    """The serial tool runs strictly between chunk-1 and chunk-2 in wall-clock time."""
    tools, calls, log = _build_mixed_batch()
    handler = make_handler(tools)

    handler.execute(calls, RecordingSink(), threading.Event())  # type: ignore[arg-type]

    chunk_1_ends = [log.window(tid)[1] for tid in CHUNK_1]
    barrier_start, barrier_end = log.window(BARRIER)
    chunk_2_starts = [log.window(tid)[0] for tid in CHUNK_2]

    assert max(chunk_1_ends) <= barrier_start, 'barrier started before chunk-1 finished'
    assert barrier_end <= min(chunk_2_starts), 'chunk-2 started before barrier finished'


def test_cancel_between_chunks_interrupts_remaining_calls() -> None:
    """A cancel set between chunks short-circuits every remaining call to [interrupted].

    Batch shape: [parallel x2] -> [Cancel] -> [parallel x3]. The serial
    Cancel tool sets the cancel_event during its own execution. When the
    handler checks `cancel_event.is_set()` at the top of the next loop
    iteration, the remaining three parallel calls funnel through
    `_run_one` and return `[interrupted]`. The Cancel tool itself still
    returns its real result — the cancel only affects what comes after.
    """
    cancel = threading.Event()

    tools = {
        'Par':    make_fast_tool('Par', safe_parallel=True),
        'Cancel': make_cancelling_tool('Cancel', safe_parallel=False, cancel_event=cancel),
    }
    handler = make_handler(tools)

    calls = [
        tool_call('p1', 'Par'),
        tool_call('p2', 'Par'),
        tool_call('cx', 'Cancel'),
        tool_call('p3', 'Par'),
        tool_call('p4', 'Par'),
        tool_call('p5', 'Par'),
    ]

    sink = RecordingSink()
    messages = handler.execute(calls, sink, cancel)  # type: ignore[arg-type]

    assert [m['tool_call_id'] for m in messages] == ['p1', 'p2', 'cx', 'p3', 'p4', 'p5']

    assert messages[0]['content'] == 'ok-Par'
    assert messages[1]['content'] == 'ok-Par'
    assert messages[2]['content'] == 'cancelled-by-Cancel'
    assert messages[3]['content'] == '[interrupted]'
    assert messages[4]['content'] == '[interrupted]'
    assert messages[5]['content'] == '[interrupted]'

    submitted = {'p1', 'p2', 'cx', 'p3', 'p4', 'p5'}

    assert set(sink.starts()) == submitted, 'every call should still emit on_tool_start'
    assert set(sink.ends()) == submitted, 'every call should still emit on_tool_end'


#     ================================
# --> Visual replay
#     ================================


def _render_gantt(log: ExecutionLog, t_zero: float) -> None:
    """Print an ASCII Gantt chart of tool execution windows."""
    entries = log.entries()

    if not entries:
        print('(no execution log entries)')

        return

    max_end = max(end for _, _, end in entries) - t_zero
    scale = CHART_WIDTH / max_end if max_end > 0 else 1.0

    print()
    print(f'Timeline (1 char ~= {1 / scale * 1000:.1f}ms, total span {max_end * 1000:.0f}ms):')
    print(' ' * 8 + '0' + '-' * (CHART_WIDTH - 2) + '>')

    for tid, start, end in entries:
        rel_start = (start - t_zero) * scale
        rel_end = (end - t_zero) * scale
        bar_start = int(rel_start)
        bar_len = max(1, int(rel_end - rel_start))

        bar = ' ' * bar_start + '#' * bar_len

        print(f'  {tid:>4}  {bar}')


def visual_replay() -> None:
    """Re-run the mixed batch with a live stdout sink and a Gantt chart."""
    tools, calls, log = _build_mixed_batch()
    handler = make_handler(tools)
    sink = StdoutSink()

    print('Submitting 7 tool calls in one batch:')

    for c in calls:
        marker = '[serial]  ' if c['function']['name'] == 'Serial' else '[parallel]'
        print(f'  {marker} id={c["id"]}  args={c["function"]["arguments"]}')

    print()
    print('--- live sink events ---')

    t_zero = time.monotonic()
    messages = handler.execute(calls, sink, threading.Event())
    elapsed = time.monotonic() - t_zero

    print('--- end sink events ---')

    serial_floor = PARALLEL_SLEEP * 6 + BARRIER_SLEEP
    optimal = PARALLEL_SLEEP + BARRIER_SLEEP + PARALLEL_SLEEP

    print()
    print(f'Total wall-clock: {elapsed * 1000:.0f}ms')
    print(f'Serial floor:     {serial_floor * 1000:.0f}ms  (if every tool ran sequentially)')
    print(f'Optimal:          {optimal * 1000:.0f}ms  (one wait per chunk + barrier)')

    _render_gantt(log, t_zero)

    print()
    print('Result-message order (must match submission order):')

    for m in messages:
        print(f'  {m["tool_call_id"]:>4}  ->  {m["content"]}')


#     ================================
# --> Runner
#     ================================


TESTS = [
    ('single-tool',     test_happy_path),
    ('single-tool',     test_tool_exception_surfaces_as_error_string),
    ('single-tool',     test_unknown_tool_name),
    ('single-tool',     test_bad_arguments_json),
    ('single-tool',     test_cancel_event_short_circuits),
    ('parallel-batch',  test_results_preserve_submission_order),
    ('parallel-batch',  test_concurrent_start_windows_overlap),
    ('parallel-batch',  test_one_tool_error_does_not_break_the_batch),
    ('parallel-batch',  test_sink_sees_every_parallel_tool),
    ('mixed-batch',     test_mixed_batch_preserves_submission_order),
    ('mixed-batch',     test_mixed_batch_each_chunk_overlaps_internally),
    ('mixed-batch',     test_mixed_batch_barrier_separates_the_chunks),
    ('cancellation',    test_cancel_between_chunks_interrupts_remaining_calls),
]


def main() -> None:
    print('=' * 64)
    print('  ToolHandler verification')
    print('=' * 64)

    by_group: dict[str, list[tuple[str, bool, str]]] = {}

    for group, fn in TESTS:
        try:
            fn()
            by_group.setdefault(group, []).append((fn.__name__, True, ''))

        except AssertionError as e:
            by_group.setdefault(group, []).append((fn.__name__, False, str(e)))

    total_pass = 0
    total = 0

    for group, results in by_group.items():
        passed = sum(1 for _, ok, _ in results if ok)

        print()
        print(f'[{group}]  {passed}/{len(results)} passed')

        for name, ok, err in results:
            mark = 'PASS' if ok else 'FAIL'
            print(f'  {mark}  {name}')

            if not ok:
                print(f'        -> {err}')

        total_pass += passed
        total += len(results)

    print()
    print('=' * 64)
    print(f'  TOTAL: {total_pass}/{total} passed')
    print('=' * 64)

    print()
    print('=' * 64)
    print('  Visual replay — mixed batch with live stdout sink')
    print('=' * 64)
    print()

    visual_replay()


if __name__ == '__main__':
    main()
