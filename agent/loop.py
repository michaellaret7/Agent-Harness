"""Streaming agentic execution loop.

Calls the model with streaming. Prints content as it arrives. If the model
asks for tools, assembles the tool_call fragments across chunks, executes
each tool, appends the results to `messages`, and calls again. Repeats
until the model returns plain content (or a safety ceiling is hit).
"""
from __future__ import annotations

from openai import OpenAI

from agent.tool_handler import ToolHandler

MODEL = 'nemotron3-nano-4b-fp8'


def execution_loop(
    client: OpenAI,
    handler: ToolHandler,
    messages: list[dict],
    max_iters: int = 10,
) -> str:
    for _ in range(max_iters):
        content, tool_calls = _stream_once(
            client, 
            messages, 
            handler.schemas() or None,
        )

        # Reconstruct the assistant turn for history. tool_calls must stay
        # paired with the role:'tool' messages we add below.
        assistant_msg: dict = {'role': 'assistant', 'content': content}

        if tool_calls:
            assistant_msg['tool_calls'] = tool_calls

        messages.append(assistant_msg)

        if not tool_calls:
            return content

        for tc in tool_calls:
            name = tc['function']['name']
            args = tc['function']['arguments']
            
            result = handler.call(name, args) # This is where the actual python function is run

            print(f'  [tool] {name}({args}) -> {result}')

            messages.append({
                'role': 'tool',
                'tool_call_id': tc['id'],
                'content': result,
            })

    raise RuntimeError(f'tool loop did not converge in {max_iters} iterations')


def _stream_once(
    client: OpenAI,
    messages: list[dict],
    tools: list[dict] | None,
) -> tuple[str, list[dict]]:
    """Stream one completion. Returns (content, tool_calls)."""

    stream = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
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
    return ''.join(content_pieces), [tool_calls[i] for i in sorted(tool_calls)]
