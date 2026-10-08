"""Sink implementations for the agent.

`Sink`, `BaseSink`, `StdoutSink`, `MultiSink`, `LogSink`, `ToolOutcome`
are re-exported here for convenience. `LangfuseSink` is intentionally NOT
re-exported: importing it pulls in the `langfuse` package, which we only
want loaded when tracing is on. Import it lazily via
`from agent_harness.sinks.langfuse import LangfuseSink`.

`register_always_on(factory)` registers an ambient sink (Langfuse,
metrics, audit) that is composed into every Agent.run alongside the
caller's presentation sink (StdoutSink, TUISink, LogSink, …). The
factory receives the running `Agent` so it can read attributes like
`provider` / `model` when constructing its sink.

LangfuseSink is auto-registered the first time `compose_sinks` or
`register_always_on` is called, when LANGFUSE_PUBLIC_KEY is present in
the environment — set the env var and tracing follows every agent. Unset
it and the framework is silent. The application entry point owns
`load_dotenv()`; this module only reads the already-populated environment.

The bootstrap is lazy, idempotent, and thread-safe. Type-only imports
(`from agent_harness.sinks import Sink`) do not trigger it; only consumers that
actually compose ambient sinks pay the cost.

All sinks created by the ambient factory share a single process-level
`SESSION_ID`, so every Agent.run within one Python process appears
under the same Langfuse session. A process started with
`LANGFUSE_SESSION_ID` set (e.g. a code-execution sandbox kernel) joins
that session instead, so agents built in model code trace alongside
their parent.
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

    if len(parts) == 1:
        return parts[0]

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
