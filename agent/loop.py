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

from agent.messages import assistant_msg, cached_text, tool_msg
from agent.sinks import Sink, StdoutSink
from agent.sinks.base import ToolOutcome
from agent.usage import Usage

if TYPE_CHECKING:
    from agent.agent import Agent

#     ================================
# --> Helper funcs: These are internal helper functions used by the loop.
#     ================================


def _extract_reasoning(obj) -> str:
    """Pull reasoning text from any of the provider shapes: `reasoning`,
    `reasoning_content`, or structured `reasoning_details[].text`."""
    text = getattr(obj, 'reasoning_content', None) or getattr(obj, 'reason', None)

    if text:
        return text

    details = getattr(obj, 'reasoning_details', None) or []
    parts: list[str] = []

    for d in details:
        t = d.get('text') if isinstance(d, dict) else getattr(d, 'text', None)

        if t:
            parts.append(t)

    return ''.join(parts)


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
        sink.on_tool_end(
            tc['id'],
            ToolOutcome(
                payload='[interrupted]', 
                status='interrupted',
                duration=0.0
            ),
        )

        agent.messages.append(tool_msg(tc['id'], '[interrupted]'))


def _classify(was_cancelled: bool, tool_calls: list[dict]) -> str:
    """Bucket an iteration's outcome into one terminal action string.

    The loop dispatches on this; sinks log it. Order matters:
    `partial_cancelled` must beat `cancelled` because a cancel during
    a partial tool-call fragment cannot be settled the same way.
    """
    if was_cancelled and _has_partial_tool_call(tool_calls):
        return 'partial_cancelled'

    if was_cancelled:
        return 'cancelled'

    if not tool_calls:
        return 'answer_ready'

    return 'tool_calls'


def _refresh_rolling_cache_breakpoint(messages: list[dict]) -> None:
    """Move the rolling cache_control marker to the last assistant/tool message.

    Strips any prior marker first so we stay at exactly 2 breakpoints
    (system anchor + rolling tail), well under Anthropic's limit of 4.
    The system message's marker is owned by `build_initial_context` and
    never touched here — the strip loop filters by role.

    Idempotent: safe to call before every LLM request.
    """
    for m in messages:
        if m['role'] not in ('assistant', 'tool'):
            continue

        content = m.get('content')

        if isinstance(content, list) and content and 'text' in content[0]:
            m['content'] = content[0]['text']

    for m in reversed(messages):
        if m['role'] not in ('assistant', 'tool'):
            continue

        content = m.get('content')

        if isinstance(content, str) and content:
            m['content'] = cached_text(content)
            return


#     ================================
# --> Loop
#     ================================


def execution_loop(
    agent: 'Agent',
    model: str,
    max_iters: int = 100,
    # --------------------------------------------
    sink: Sink | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Main agent execution loop.

    This function runs the agent for a maximum of `max_iters` iterations.
    In each iteration, it streams an LLM call, processes the response
    (handling tool calls and cancellations), and continues until the LLM
    returns content without tool calls or a cancellation is requested.

    Args:
        agent: The agent instance, containing messages, tools, and tool_handler.
        model: The model name to use for the LLM call.
        max_iters: Maximum number of iterations to run.
        sink: Optional sink for UI updates (defaults to StdoutSink).
        cancel_event: Optional event to signal cancellation (defaults to a new Event).

    Returns:
        The final content string from the agent.
    """

    active_sink: Sink = sink if sink is not None else StdoutSink()
    active_cancel: threading.Event = cancel_event if cancel_event is not None else threading.Event()

    last_content = ''

    # Activate langfuse loop span to track the loop iterations and tool calls
    active_sink.on_loop_start(model, max_iters, [t['function']['name'] for t in agent.tools]) 

    # Begin execution loop
    for i in range(1, max_iters + 1):

        if active_cancel.is_set():
            active_sink.on_interrupted() # this line breaks the loop because the user has cancelled the execution
            break
        
        # Activate langfuse iteration span 
        active_sink.on_iteration_start(i, len(agent.messages))

        # Call the LLM aka the completions api and stream the response
        content, tool_calls, was_cancelled, usage = call_llm(
            agent.client,
            agent.messages,
            agent.tools,
            model,
            active_sink,
            active_cancel,
        )

        if usage is not None:
            active_sink.on_usage(usage) # send the llm useage to the sink protocol

        last_content = content

        # Bucket the iteration outcome (normal / cancelled / partial_cancelled) so the loop can dispatch and sinks can log it
        action = _classify(was_cancelled, tool_calls)
        # Pull just the tool names from the tool calls for lightweight logging (args/ids are dropped)
        tool_names = [tc['function']['name'] for tc in tool_calls]
        active_sink.on_iteration_end(i, action, content, tool_names)

        if action == 'partial_cancelled':
            active_sink.on_interrupted()
            break

        # Append the assistant message to the agents state aka the message history as an assistant message
        # Include the content from the llm response and the tool calls returned from the llm 
        # The tool calls will be executed downstream by the tool handler and then appended to the message history as tool output messages
        agent.messages.append(assistant_msg(content, tool_calls))

        if action == 'cancelled':
            _settle_interrupted_tool_calls(agent, tool_calls, active_sink)
            active_sink.on_interrupted()
            break

        if action == 'answer_ready':
            active_sink.on_loop_end('answer_ready', i)
            return content

        # Add the output results of the tool calls to the agents state as tool output messages
        # The tool handler class executes the tool calls and returns the output results as tool output messages
        agent.messages.extend(agent.tool_handler.execute(tool_calls, active_sink, active_cancel))

    active_sink.on_loop_end('max_iterations', max_iters)

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
    cancel_event: threading.Event,
) -> tuple[str, list[dict], bool, Usage | None]:
    """Call the LLM with streaming. Returns (content, tool_calls, was_cancelled, usage)."""

    _refresh_rolling_cache_breakpoint(messages)

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice='auto',
        stream=True,
        # Final chunk arrives with empty choices and populated usage.
        stream_options={'include_usage': True},
    )

    content_pieces: list[str] = []
    # Tool calls arrive in fragments keyed by index — id/name appear on the
    # first fragment; arguments accumulate across the rest.
    tool_call_slots: dict[int, dict] = {}
    was_cancelled = False
    usage: Usage | None = None

    for chunk in response:
        if cancel_event.is_set():
            was_cancelled = True
            response.close()
            break

        # The final usage-only chunk has empty choices but a populated
        # `usage` field. Capture it before the `not chunk.choices` skip.
        if getattr(chunk, 'usage', None):
            usage = Usage.from_response(chunk.usage)

        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta

        # Surface reasoning live; do not keep it in history.
        reasoning = _extract_reasoning(delta)

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

    return content, tool_calls, was_cancelled, usage