"""Organization delivery contracts: per-member inboxes run in parallel, each member serially.

Members are stand-in agents whose `run` sleeps and records its time window, so
the tests grade real Organization scheduling and the real SendMessage tool
without a model in the loop.
"""
from __future__ import annotations

import _thread
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from architectures import Organization

RUN_SECONDS = 0.3


#     ================================
# --> Helper funcs
#     ================================


class TimedAgent:
    """Minimal agent surface the org needs; `run` sleeps, then calls `on_run` with the prompt."""

    def __init__(self, on_run: Callable[['TimedAgent', str], None] | None = None) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}
        self.windows: list[tuple[float, float]] = []
        self.prompts: list[str] = []
        self._on_run = on_run
        self._lock = threading.Lock()

    def extend_system_prompt(self, text: str) -> None:
        pass

    def add_tool(self, tool: dict[str, Any]) -> None:
        self.tools[tool['name']] = tool['function']

    def run(self, prompt: str, sink: Any, cancel_event: threading.Event) -> str:
        start = time.monotonic()

        # Like the real loop, a cancel ends the run early and records nothing
        if cancel_event.wait(RUN_SECONDS):
            return 'cancelled'

        if self._on_run is not None:
            self._on_run(self, prompt)

        with self._lock:
            self.windows.append((start, time.monotonic()))
            self.prompts.append(prompt)

        return 'done'

    def send(self, recipient_id: uuid.UUID, content: str) -> tuple[str, str]:
        result = self.tools['SendMessage'](recipient_id=str(recipient_id), content=content)

        return result.payload, result.status


def overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


@pytest.fixture
def org(monkeypatch: pytest.MonkeyPatch) -> Organization:
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'test-key')  # register_agent requires tracing to be configured

    return Organization(name='Test Org', goal='Exercise delivery.')


#     ================================
# --> Contracts
#     ================================


def test_idle_members_run_in_parallel_while_a_busy_member_works_its_queue(org: Organization) -> None:
    ceo, analyst, risk = TimedAgent(), TimedAgent(), TimedAgent()
    ceo_id = org.register_agent('ceo', ceo)
    analyst_id = org.register_agent('analyst', analyst)
    risk_id = org.register_agent('risk', risk)

    started = time.monotonic()

    org.run([(ceo_id, 'one'), (ceo_id, 'two'), (ceo_id, 'three'), (analyst_id, 'data'), (risk_id, 'review')])

    elapsed = time.monotonic() - started

    # The CEO handles its three messages one at a time, in arrival order
    assert [p.splitlines()[-1] for p in ceo.prompts] == ['one', 'two', 'three']
    assert not any(overlaps(a, b) for i, a in enumerate(ceo.windows) for b in ceo.windows[i + 1:])

    # Idle members do not wait behind the CEO's backlog
    assert overlaps(analyst.windows[0], ceo.windows[0])
    assert overlaps(risk.windows[0], ceo.windows[0])

    # Wall time is the CEO's serial chain, not the sum of all five runs
    assert elapsed < 4 * RUN_SECONDS


def test_message_to_a_running_member_waits_until_its_run_ends(org: Organization) -> None:
    ids: dict[str, uuid.UUID] = {}
    replies: list[tuple[str, str]] = []

    def ask_manager_back(agent: TimedAgent, prompt: str) -> None:
        if 'from org' in prompt:
            replies.append(agent.send(ids['manager'], 'report'))

    manager = TimedAgent()
    analyst = TimedAgent(on_run=ask_manager_back)
    ids['manager'] = org.register_agent('manager', manager)
    ids['analyst'] = org.register_agent('analyst', analyst)

    # The manager's first run is long enough that the analyst's report arrives while it is busy
    org.run([(ids['manager'], 'plan'), (ids['manager'], 'plan more'), (ids['analyst'], 'research')])

    assert replies == [(f"Queued for manager ({ids['manager']}).", 'ok')]
    assert [p.splitlines()[-1] for p in manager.prompts] == ['plan', 'plan more', 'report']
    assert manager.windows[2][0] >= manager.windows[1][1]
    assert f"analyst ({ids['analyst']})" in manager.prompts[2]


def test_send_message_errors_once_the_run_budget_is_spent(org: Organization) -> None:
    results: list[tuple[str, str]] = []
    ids: dict[str, uuid.UUID] = {}

    def spam(agent: TimedAgent, prompt: str) -> None:
        for n in range(3):
            results.append(agent.send(ids['sink'], f'msg {n}'))

    ids['talker'] = org.register_agent('talker', TimedAgent(on_run=spam))
    ids['sink'] = org.register_agent('sink', TimedAgent())

    org.run([(ids['talker'], 'go')], max_messages=3)

    assert [status for _, status in results] == ['ok', 'ok', 'error']
    assert 'Org message limit (3) reached' in results[2][0]
    assert len(org.get_agent(ids['sink']).prompts) == 2


def test_a_failing_member_surfaces_its_error_without_hanging_the_org(org: Organization) -> None:
    def explode(agent: TimedAgent, prompt: str) -> None:
        raise RuntimeError('model provider down')

    broken_id = org.register_agent('broken', TimedAgent(on_run=explode))
    healthy = TimedAgent()
    healthy_id = org.register_agent('healthy', healthy)

    with pytest.raises(RuntimeError, match='model provider down'):
        org.run([(broken_id, 'work'), (healthy_id, 'work')])

    assert len(healthy.prompts) == 1


def test_kickoff_is_validated_before_any_member_runs(org: Organization) -> None:
    member = TimedAgent()
    member_id = org.register_agent('member', member)

    with pytest.raises(ValueError, match='not members'):
        org.run([(member_id, 'work'), (uuid.uuid4(), 'work')])

    with pytest.raises(ValueError, match='exceed max_messages'):
        org.run([(member_id, 'a'), (member_id, 'b')], max_messages=1)

    assert member.prompts == []


def test_ctrl_c_stops_running_agents_skips_the_backlog_and_frees_the_org(org: Organization) -> None:
    busy = TimedAgent()
    busy_id = org.register_agent('busy', busy)

    # Ctrl+C lands while the first message runs and two more are queued behind it
    threading.Timer(RUN_SECONDS / 2, _thread.interrupt_main).start()

    started = time.monotonic()

    with pytest.raises(KeyboardInterrupt):
        org.run([(busy_id, 'one'), (busy_id, 'two'), (busy_id, 'three')])

    assert time.monotonic() - started < RUN_SECONDS * 2
    assert busy.prompts == []
    assert not [t for t in threading.enumerate() if t.name.startswith('Test Org:')]

    # The org is reusable: no leftover messages or counts leak into the next run
    org.run([(busy_id, 'again')])

    assert [p.splitlines()[-1] for p in busy.prompts] == ['again']
