"""Ambient-sink bootstrap contract: concurrent first runs all get the Langfuse sink."""
from __future__ import annotations

import sys
import threading
import time
import types
from typing import Any

import pytest

import agent_harness.sinks as sinks
from agent_harness.sinks import MultiSink, StdoutSink


#     ================================
# --> Helper funcs
#     ================================


class SlowLangfuseModule(types.ModuleType):
    """Stand-in for `agent_harness.sinks.langfuse` whose import is slow, widening the race window."""

    def __getattr__(self, name: str) -> Any:
        if name != 'LangfuseSink':
            raise AttributeError(name)

        time.sleep(0.2)

        return lambda **kwargs: StdoutSink()


class FakeAgent:
    provider = 'openrouter'
    model = 'test-model'


#     ================================
# --> Contracts
#     ================================


def test_concurrent_first_compose_both_include_the_ambient_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'test-key')
    monkeypatch.setattr(sinks, '_bootstrapped', False)
    monkeypatch.setattr(sinks, '_always_on', [])
    monkeypatch.setitem(sys.modules, 'agent_harness.sinks.langfuse', SlowLangfuseModule('agent_harness.sinks.langfuse'))

    composed: list[Any] = []

    def first_run() -> None:
        composed.append(sinks.compose_sinks(FakeAgent(), StdoutSink()))  # type: ignore[arg-type]

    threads = [threading.Thread(target=first_run) for _ in range(2)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    # Each run is presentation sink + Langfuse; a lost race returns the bare presentation sink
    assert len(composed) == 2
    assert all(isinstance(sink, MultiSink) for sink in composed)
    assert len(sinks._always_on) == 1
