"""Streaming agentic execution loop.

Calls the model with streaming. Pushes deltas to the Sink. If the model
asks for tools, assembles the tool_call fragments across chunks, executes
each tool, appends the results to `messages`, and calls again. Repeats
until the model returns plain content (or a safety ceiling is hit).

Cancellation: `cancel_event` (threading.Event) is checked at iteration
boundaries AND inside the chunk loop. On cancel: the active stream is
abandoned, partial state is fixed up so `messages` stays well-formed, and
the loop returns whatever content was accumulated.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from openai import OpenAI

from tui.sink import Sink, StdoutSink

if TYPE_CHECKING:
    from agent.agent import Agent

#     ================================
# --> Helper funcs
#     ================================


def _has_partial_tool_call(tool_calls: list[dict]) -> bool:
    """A tool_call missing id/name is malformed and unusable for the next request."""
    for tc in tool_calls:
        if not tc.get('id'):
            return True

        fn = tc.get('function') or {}

        if not fn.get('name'):
            return True

    return False


def _settle_interrupted_tool_calls(agent: 'Agent', tool_calls: list[dict], sink: Sink) -> None:
    """Append synthetic tool-result messages so tool_call/tool_result pairs stay matched."""
    for tc in tool_calls:
        sink.on_tool_end(tc['id'], '[interrupted]')

        agent.messages.append({
            'role': 'tool',
            'tool_call_id': tc['id'],
            'content': '[interrupted]',
        })

#     ================================
# --> Loop
#     ================================


def execution_loop(
    agent: 'Agent',
    model: str,
    max_iters: int = 100,
    stream: bool = False,
    # --------------------------------------------
    sink: Sink | None = None,
    cancel_event: threading.Event | None = None,
) -> str:

    active_sink: Sink = sink if sink is not None else StdoutSink()
    active_cancel: threading.Event = cancel_event if cancel_event is not None else threading.Event()

    last_content = ''

    for _ in range(max_iters):
        
        if active_cancel.is_set():
            active_sink.on_interrupted()
            break

        if stream:
            content, tool_calls, was_cancelled = call_llm_stream(
                agent.client,
                agent.messages,
                agent.tools,
                model,
                active_sink,
                active_cancel,
            )
        else:
            content, tool_calls = call_llm(
                agent.client,
                agent.messages,
                agent.tools,
                model,
                active_sink,
            )
            was_cancelled = False

        last_content = content

        if was_cancelled and _has_partial_tool_call(tool_calls):
            active_sink.on_interrupted()
            break

        assistant_msg: dict = {'role': 'assistant', 'content': content}

        if tool_calls:
            assistant_msg['tool_calls'] = tool_calls

        agent.messages.append(assistant_msg)

        if was_cancelled:
            _settle_interrupted_tool_calls(agent, tool_calls, active_sink)
            active_sink.on_interrupted()
            break

        if not tool_calls:
            return content

        agent.messages.extend(agent.tool_handler.execute(tool_calls, active_sink, active_cancel))

    return last_content

#     ================================
# --> LLM calls
#     ================================


def call_llm(
    client: OpenAI,
    messages: list[dict],
    tools: list[dict] | None,
    model: str,
    sink: Sink,
) -> tuple[str, list[dict]]:
    """Call the LLM (non-stream) and return (content, tool_calls) as plain dicts."""

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice='auto',
        stream=False,
    )

    message = response.choices[0].message
    content = message.content or ''

    reasoning = getattr(message, 'reasoning', None)

    if reasoning:
        sink.on_reasoning_delta(reasoning)

    if content:
        sink.on_content_delta(content)

    sink.on_assistant_end()

    tool_calls: list[dict] = []

    for tc in message.tool_calls or []:
        tool_calls.append({
            'id': tc.id,
            'type': 'function',
            'function': {
                'name': tc.function.name,
                'arguments': tc.function.arguments or '',
            },
        })

    return content, tool_calls


def call_llm_stream(
    client: OpenAI,
    messages: list[dict],
    tools: list[dict] | None,
    model: str,
    sink: Sink,
    cancel_event: threading.Event,
) -> tuple[str, list[dict], bool]:
    """Call the LLM with streaming. Returns (content, tool_calls, was_cancelled)."""

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice='auto',
        stream=True,
    )

    content_pieces: list[str] = []
    # Tool calls arrive in fragments keyed by index — id/name appear on the
    # first fragment; arguments accumulate across the rest.
    tool_call_slots: dict[int, dict] = {}
    was_cancelled = False

    for chunk in response:
        if cancel_event.is_set():
            was_cancelled = True
            response.close()
            break

        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta

        # Surface reasoning live; do not keep it in history.
        reasoning = getattr(delta, 'reasoning_content', None)

        if reasoning:
            sink.on_reasoning_delta(reasoning)

        if delta.content:
            content_pieces.append(delta.content)
            sink.on_content_delta(delta.content)

        if delta.tool_calls:
            for tc in delta.tool_calls:
                slot = tool_call_slots.setdefault(tc.index, {
                    'id': '', 'type': 'function',
                    'function': {'name': '', 'arguments': ''},
                })

                if tc.id:
                    slot['id'] = tc.id

                if tc.function:
                    if tc.function.name:
                        slot['function']['name'] = tc.function.name

                    if tc.function.arguments:
                        slot['function']['arguments'] += tc.function.arguments

    sink.on_assistant_end()

    content = ''.join(content_pieces)
    tool_calls: list[dict] = [tool_call_slots[i] for i in sorted(tool_call_slots)]

    return content, tool_calls, was_cancelled
