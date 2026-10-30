"""Sink exports and automatic composition of ambient sinks.

`register_always_on(factory)` adds an Agent-aware sink factory to every run.
Langfuse registers lazily when LANGFUSE_PUBLIC_KEY is set; bootstrap is
idempotent and thread-safe. Applications load the environment themselves.
Import LangfuseSink from `sinks.langfuse` only when needed.

Runs share a process SESSION_ID, or inherit LANGFUSE_SESSION_ID from the
environment so sandbox agents can join their parent's tracing session.
"""
from __future__ import annotations

import os
import threading
import uuid
from typing import TYPE_CHECKING, Callable, cast

from agent_harness.sinks.log import LogSink, configure_logging
from agent_harness.sinks.base import BaseSink, MultiSink, Sink, ToolOutcome
from agent_harness.sinks.stdout import StdoutSink

if TYPE_CHECKING:
    from agent_harness.agent import Agent


_always_on: list[Callable[['Agent'], Sink]] = []
SESSION_ID = os.environ.get('LANGFUSE_SESSION_ID') or uuid.uuid4().hex
_bootstrapped = False
_bootstrap_lock = threading.Lock()  # concurrent first runs (e.g. Organization workers) must all see the registration


def _ensure_bootstrapped() -> None:
    """Run one-time Langfuse auto-registration.

    Called on the first use of `compose_sinks` or `register_always_on`,
    not at import time, so type-only consumers of this module pay nothing.
    Idempotent: subsequent calls are no-ops.

    Reads the already-populated environment — the application entry point
    owns `load_dotenv()`, so `LANGFUSE_PUBLIC_KEY` is visible here by the
    time any Agent is constructed and run.
    """
    global _bootstrapped

    # Held for the whole bootstrap: a second thread waits here until the factory is appended,
    # instead of seeing `_bootstrapped` set early and composing a run without Langfuse
    with _bootstrap_lock:
        if _bootstrapped:
            return

        _bootstrapped = True

        if not os.environ.get('LANGFUSE_PUBLIC_KEY'):
            return

        from agent_harness.sinks.langfuse import LangfuseSink

        def factory(agent: 'Agent') -> Sink:
            return LangfuseSink(
                session_id=SESSION_ID,
                name=agent.name,
                metadata={'provider': agent.provider, 'model': agent.model},
            history=agent.messages,
            )

        _always_on.append(factory)


def register_always_on(factory: Callable[['Agent'], Sink]) -> None:
    """Register a sink factory composed into every Agent.run.

    Call once at startup for ambient observability (LangfuseSink, etc).
    Factory runs per-run with the active Agent so each instance gets a
    fresh sink keyed to the agent's provider/model.
    """
    _ensure_bootstrapped()
    _always_on.append(factory)


def compose_sinks(agent: 'Agent', sink: Sink) -> Sink:
    _ensure_bootstrapped()

    parts: list[Sink] = [sink] + [f(agent) for f in _always_on]

    # Always wrapped, even with one sink: MultiSink isolates a raising sink, and that
    # isolation must not depend on whether tracing or hooks happen to be enabled
    return cast(Sink, MultiSink(parts))


__all__ = [
    'BaseSink',
    'LogSink',
    'MultiSink',
    'SESSION_ID',
    'Sink',
    'StdoutSink',
    'ToolOutcome',
    'configure_logging',
    'compose_sinks',
    'register_always_on',
]
