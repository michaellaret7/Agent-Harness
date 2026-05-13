"""Sink implementations for the agent.

`Sink`, `StdoutSink`, `MultiSink` are re-exported here for convenience.
`LangfuseSink` is intentionally NOT re-exported: importing it pulls in
the `langfuse` package, which we only want loaded when tracing is on.
Import it lazily via `from agent.sinks.langfuse import LangfuseSink`.
"""
from agent.sinks.base import MultiSink, Sink, StdoutSink

__all__ = ['MultiSink', 'Sink', 'StdoutSink']
