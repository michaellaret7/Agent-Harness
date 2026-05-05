"""Streaming agentic execution loop.

Calls the model with streaming. Prints content as it arrives. If the model
asks for tools, assembles the tool_call fragments across chunks, executes
each tool, appends the results to `messages`, and calls again. Repeats
until the model returns plain content (or a safety ceiling is hit).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from openai import OpenAI

if TYPE_CHECKING:
    from agent.agent import Agent


def execution_loop(
    agent: 'Agent',
    model: str,
    max_iters: int = 10,
    stream: bool = False,
) -> str:

    for iteration in range(1, max_iters + 1):
        if stream:
            content, tool_calls = call_llm_stream(
                agent.client,
                agent.messages,
                agent.tools,
                model,
            )
        else:
            content, tool_calls = call_llm(
                agent.client,
                agent.messages,
                agent.tools,
                model,
            )

        # Reconstruct the assistant turn for history. tool_calls must stay
        # paired with the role:'tool' messages we add below.
        assistant_msg: dict = {'role': 'assistant', 'content': content}

        if tool_calls:
            assistant_msg['tool_calls'] = tool_calls

        agent.messages.append(assistant_msg)

        if not tool_calls:
            return content

        agent.messages.extend(agent.tool_handler.execute(tool_calls))

    raise RuntimeError(f'tool loop did not converge after {iteration} iterations')

def call_llm(
    client: OpenAI,
    messages: list[dict],
    tools: list[dict] | None,
    model: str,
) -> tuple[str, list[dict]]:
    """Call the LLM and return (content, tool_calls) as plain dicts."""

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice='auto',
        stream=False,
    )

    message = response.choices[0].message
    content = message.content or ''

    if message.reasoning:
        print('='*200)
        print(f'\033[90m{message.reasoning}\033[0m', end='', flush=True)
        print('='*200)

    if content:
        print(content)

    # Normalize SDK objects to dicts so the loop can treat stream/non-stream
    # results identically and append them straight back into `messages`.
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
) -> tuple[str, list[dict]]:
    """Call the LLM and stream the response."""

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice='auto',
        # temperature=0.6,
        # top_p=0.95,
        stream=True,
    )

    content_pieces: list[str] = []
    # Tool calls arrive in fragments keyed by index — id/name appear on the
    # first fragment; arguments accumulate across the rest.
    tool_calls: dict[int, dict] = {}

    for chunk in stream:
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta

        # Surface reasoning live; do not keep it in history.
        reasoning = getattr(delta, 'reasoning_content', None)

        if reasoning:
            print(reasoning, end='', flush=True)

        if delta.content:
            content_pieces.append(delta.content)
            print(delta.content, end='', flush=True)

        if delta.tool_calls:
            for tc in delta.tool_calls:
                slot = tool_calls.setdefault(tc.index, {
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

    print()

    content = ''.join(content_pieces)
    tool_calls = [tool_calls[i] for i in sorted(tool_calls)]

    return content, tool_calls
