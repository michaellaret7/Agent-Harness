"""Offline provider fixtures and event capture for executable contract tests."""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from openai import OpenAI

from agent_harness import Agent
from agent_harness.sinks.base import BaseSink, ToolOutcome
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult

StreamItem = dict[str, Any] | Callable[[], None]


#     ================================
# --> Helper funcs
#     ================================


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    """Build a complete model-facing tool call with a stable ID."""

    return {'id': call_id, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(args),
    }}


def tool_delta(*calls: dict[str, Any]) -> dict[str, Any]:
    """Put complete calls into the wire delta consumed by the SDK."""

    return {'tool_calls': [dict(call, index=i) for i, call in enumerate(calls)]}


def message_text(message: dict[str, Any]) -> str:
    """Read message text without depending on optional cache-control wrappers."""
    content = message['content']

    return content if isinstance(content, str) else ''.join(part['text'] for part in content)


def arithmetic_tools(calls: list[tuple[str, int, int]]) -> list[Callable]:
    """Provide real arithmetic tools and record their observable invocations."""
    @agent_tool
    def add(a: int, b: int) -> ToolResult:
        """Add two integers."""
        calls.append(('add', a, b))

        return ToolResult(str(a + b), status='ok')

    @agent_tool
    def multiply(a: int, b: int) -> ToolResult:
        """Multiply two integers."""
        calls.append(('multiply', a, b))

        return ToolResult(str(a * b), status='ok')

    return [add, multiply]


@contextmanager
def scripted_agent(
    responses: Sequence[Sequence[StreamItem]],
    tools: Sequence[Callable] | None = None,
    max_iters: int = 100,
) -> Iterator[tuple[Agent, ScriptedProvider, RecordingSink]]:
    """Run real harness code against an in-memory HTTP provider, without credentials."""
    provider = ScriptedProvider(responses)
    sink = RecordingSink()

    # Disable automatic remote tracing; the only model transport is in memory.
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv('LANGFUSE_PUBLIC_KEY', '')

        with httpx.Client(transport=httpx.MockTransport(provider.respond)) as http_client:
            with OpenAI(api_key='test-only', base_url='https://harness.invalid/v1',
                        http_client=http_client, max_retries=0) as client:
                agent = Agent(model='contract-model', tools=list(tools or ()), max_iters=max_iters)
                agent.client = client

                yield agent, provider, sink


class ScriptedStream(httpx.SyncByteStream):
    """Yield real SSE bytes, with callbacks at explicit stream boundaries."""

    def __init__(self, items: Sequence[StreamItem]) -> None:
        self.items = items
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for item in self.items:
            if callable(item):
                item()
                continue

            chunk = {'id': 'completion-test', 'object': 'chat.completion.chunk',
                     'created': 0, 'model': 'contract-model',
                     'choices': [{'index': 0, 'delta': item, 'finish_reason': None}]}

            yield f'data: {json.dumps(chunk)}\n\n'.encode()

        yield b'data: [DONE]\n\n'

    def close(self) -> None:
        """Record stream closure for cancellation assertions."""
        self.closed = True


class ScriptedProvider:
    """Capture serialized requests and return the next scripted SSE response."""

    def __init__(self, responses: Sequence[Sequence[StreamItem]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []
        self.streams: list[ScriptedStream] = []

    def respond(self, request: httpx.Request) -> httpx.Response:
        """Handle a model request without opening a socket."""
        self.requests.append(json.loads(request.content))
        index = len(self.requests) - 1
        assert index < len(self.responses), 'harness made an unexpected extra model request'
        stream = ScriptedStream(self.responses[index])
        self.streams.append(stream)

        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)


class RecordingSink(BaseSink):
    """Capture public events in delivery order, including parallel tool events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.lock = threading.Lock()

    def record(self, name: str, value: Any = None) -> None:
        """Append an event atomically."""
        with self.lock:
            self.events.append((name, value))

    def values(self, name: str) -> list[Any]:
        """Return the recorded values for one event type."""
        with self.lock:
            values = [value for event, value in self.events if event == name]

        return values

    def on_reasoning_delta(self, text: str) -> None:
        """Record streamed reasoning."""
        self.record('reasoning', text)

    def on_content_delta(self, text: str) -> None:
        """Record streamed answer text."""
        self.record('content', text)

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        """Record the start of a tool call."""
        self.record('tool_start', (tool_call_id, name, args_json))

    def on_tool_end(self, tool_call_id: str, outcome: ToolOutcome) -> None:
        """Record the outcome of a tool call."""
        self.record('tool_end', (tool_call_id, outcome))

    def on_loop_end(self, stop_reason: str, iterations: int) -> None:
        """Record the loop's terminal reason and request count."""
        self.record('loop_end', (stop_reason, iterations))

    def on_interrupted(self) -> None:
        """Record cancellation notification."""
        self.record('interrupted')

    def on_turn_end(self, result: str) -> None:
        """Record the completed turn's text."""
        self.record('turn_end', result)
