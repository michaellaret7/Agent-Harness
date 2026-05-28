"""Sink implementations for the agent.

`Sink`, `StdoutSink`, `MultiSink`, `LogSink` are re-exported here for
convenience. `LangfuseSink` is intentionally NOT re-exported: importing
it pulls in the `langfuse` package, which we only want loaded when
tracing is on. Import it lazily via
`from agent.sinks.langfuse import LangfuseSink`.

`register_always_on(factory)` registers an ambient sink (Langfuse,
metrics, audit) that is composed into every Agent.run alongside the
caller's presentation sink (StdoutSink, TUISink, LogSink, …). The
factory receives the running `Agent` so it can read attributes like
`provider` / `model` when constructing its sink.

LangfuseSink is auto-registered at import time when LANGFUSE_PUBLIC_KEY
is present in the environment — set the env var and tracing follows
every agent. Unset it (or run without `.env`) and the framework is
silent. `.env` is loaded here so the check sees `.env`-only credentials.

All sinks created by the ambient factory share a single process-level
`_SESSION_ID`, so every Agent.run within one Python process appears
under the same Langfuse session.
"""
from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Callable, cast

from dotenv import load_dotenv

from agent.sinks.log import LogSink, configure_logging
from agent.sinks.protocol import MultiSink, Sink
from agent.sinks.stdout import StdoutSink

if TYPE_CHECKING:
    from agent.agent import Agent


_always_on: list[Callable[['Agent'], Sink]] = []
_SESSION_ID = uuid.uuid4().hex


def register_always_on(factory: Callable[['Agent'], Sink]) -> None:
    """Register a sink factory composed into every Agent.run.

    Call once at startup for ambient observability (LangfuseSink, etc).
    Factory runs per-run with the active Agent so each instance gets a
    fresh sink keyed to the agent's provider/model.
    """
    _always_on.append(factory)


def wrap_with_ambient(agent: 'Agent', sink: Sink) -> Sink:
    parts: list[Sink] = [sink] + [f(agent) for f in _always_on]

    if len(parts) == 1:
        return parts[0]

    return cast(Sink, MultiSink(parts))


__all__ = [
    'LogSink',
    'MultiSink',
    'Sink',
    'StdoutSink',
    'configure_logging',
    'register_always_on',
    'wrap_with_ambient',
]


def _auto_register_langfuse() -> None:
    """Register LangfuseSink as an ambient sink if credentials are present.

    Runs once at import time. No-op when `LANGFUSE_PUBLIC_KEY` is unset
    (e.g. running without a `.env`) — the framework stays silent. The
    factory is closed over `_SESSION_ID` so every Agent.run in this
    process appears under a single Langfuse session.
    """
    if not os.environ.get('LANGFUSE_PUBLIC_KEY'):
        return

    from agent.sinks.langfuse import LangfuseSink

    def factory(agent: 'Agent') -> Sink:
        return LangfuseSink(
            session_id=_SESSION_ID,
            metadata={'provider': agent.provider, 'model': agent.model},
        )

    register_always_on(factory)


# `load_dotenv` runs first because `agent/sinks/__init__.py` is imported
# before any caller has a chance to load env vars themselves — without
# this, the `LANGFUSE_PUBLIC_KEY` check inside `_auto_register_langfuse`
# would miss credentials that live only in `.env`.
load_dotenv()
_auto_register_langfuse()
