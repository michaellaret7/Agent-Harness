"""LogSink — emits agent events to Python's logging module.

Use this when running the agent headlessly in a pipeline or multi-agent
workflow. Each LogSink owns a named logger (`agent.<name>`); the host
process owns handlers, format, and destination — the sink only emits
records. Filtering by logger name lets parallel-agent logs be
untangled without parsing message content.

Events emitted:
- INFO    turn.start  / turn.end
- INFO    loop.start  / loop.end
- INFO    tool.start  / tool.end
- WARNING interrupted
- ERROR   error

Content deltas, reasoning, iteration boundaries, plan updates and usage
events are intentionally silent — usage is accumulated internally and
folded into the turn.end summary line.
"""
from __future__ import annotations

import logging
import sys
import time

from agent.sinks.helpers import format_args_inline, format_tool_summary
from agent.sinks.base import BaseSink, ToolOutcome
from agent.usage import Usage


#     ================================
# --> Helper funcs
#     ================================


_DEFAULT_FORMAT = '%(asctime)s %(levelname)-5s %(name)s: %(message)s'
_DEFAULT_DATEFMT = '%H:%M:%S'


def _format_duration(seconds: float) -> str:
    """Compact wall-clock duration. <60s → `1.8s`; ≥60s → `2m14s`."""
    if seconds < 60:
        return f'{seconds:.1f}s'

    minutes, secs = divmod(seconds, 60)

    return f'{int(minutes)}m{int(secs):02d}s'


def configure_logging(level: int | str = 'INFO') -> None:
    """One-call logging setup for agent runs.

    Configures the `agent` logger (parent of every `agent.<name>` logger
    LogSink creates) — does not touch the root logger, so third-party
    libraries keep their existing levels. Idempotent: a second call
    replaces handlers instead of stacking them.

    `level` accepts a name (`'DEBUG'`, `'INFO'`, `'WARNING'`, …) or an
    int — callers can pick a level without `import logging`. Output goes
    to stderr — the standard place for application logs.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT))

    logger = logging.getLogger('agent')
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


#     ================================
# --> Sink
#     ================================


class LogSink(BaseSink):
    """Sink that routes agent events through `logging.getLogger('agent.<name>')`.

    The `name` argument identifies this agent in the log stream. Pick a
    name that's unique across the agents in your pipeline so callers can
    filter by logger name (e.g. `logging.getLogger('agent.researcher')`).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.log = logging.getLogger(f'agent.{name}')

        # Bootstrap default handlers if the host hasn't already wired up
        # logging. `hasHandlers()` walks the parent chain, so this is a
        # no-op when the user called `configure_logging()` themselves,
        # when a framework already configured root logging, or when any
        # ancestor logger has custom handlers. Only kicks in for the
        # "I just want logs to appear" case.
        if not self.log.hasHandlers():
            configure_logging()

        self._turn_start: float | None = None
        self._turn_usage: Usage = Usage.zero()
        self._iterations: int = 0
        self._tool_count: int = 0
        self._tool_names: dict[str, str] = {}

    #     ================================
    # --> Turn boundaries
    #     ================================

    def on_turn_start(self, task: str) -> None:
        self._turn_start = time.perf_counter()
        self._turn_usage = Usage.zero()
        self._iterations = 0
        self._tool_count = 0
        self._tool_names.clear()

        self.log.info('turn.start')

    def on_turn_end(self, result: str) -> None:
        duration = (
            time.perf_counter() - self._turn_start
            if self._turn_start is not None
            else 0.0
        )

        u = self._turn_usage

        parts = [
            f'iters={self._iterations}',
            f'tools={self._tool_count}',
            f'dur={_format_duration(duration)}',
            f'in={u.prompt_tokens}',
            f'out={u.completion_tokens}',
        ]

        # cached + cost are provider-dependent (cached: prompt caching on;
        # cost: OpenRouter only — vLLM returns 0). Omit when zero to keep
        # the line clean across providers.
        if u.cached_tokens:
            parts.append(f'cached={u.cached_tokens}')

        if u.cost:
            parts.append(f'cost=${u.cost:.4f}')

        self.log.info('turn.end %s', ' '.join(parts))

    #     ================================
    # --> Loop boundaries
    #     ================================

    def on_loop_start(self, model: str, max_iters: int, tool_names: list[str]) -> None:
        self.log.info(
            'loop.start model=%s max_iters=%d tools=%d',
            model, max_iters, len(tool_names),
        )

    def on_loop_end(self, stop_reason: str, iterations: int) -> None:
        self._iterations = iterations

        self.log.info('loop.end stop_reason=%s iters=%d', stop_reason, iterations)

    def on_iteration_start(self, number: int, message_count: int) -> None:
        self.log.info('iter.start n=%d msgs=%d', number, message_count)

    #     ================================
    # --> Tool spans
    #     ================================

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        self._tool_names[tool_call_id] = name
        self._tool_count += 1

        args = format_args_inline(args_json)

        self.log.info('tool.start %s(%s)', name, args)

    def on_tool_end(self, tool_call_id: str, outcome: ToolOutcome) -> None:
        name = self._tool_names.pop(tool_call_id, '?')

        summary = format_tool_summary(outcome)

        self.log.info('tool.end %s %s', name, summary)

    #     ================================
    # --> Diagnostics
    #     ================================

    def on_error(self, message: str) -> None:
        self.log.error('error: %s', message)

    def on_interrupted(self) -> None:
        self.log.warning('interrupted')

    def on_usage(self, usage: Usage) -> None:
        self._turn_usage = self._turn_usage + usage
