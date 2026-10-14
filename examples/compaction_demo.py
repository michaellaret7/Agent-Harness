"""Live check of context compaction: preload a long history, shrink the window, watch it compact.

The agent starts with 150 quote lookups already in its history (~27k estimated
tokens) and a context window of 30k, so the first model call compacts. The window
must leave room for what compaction cannot shrink: the tool schemas (~5.6k tokens
with a workspace attached) plus the system prompt. The task then asks for a number
only the archive holds verbatim, so a good run shows the agent reading
`history/<session>/segment_001.jsonl` with ExecuteCode.

Setup: uv sync --all-packages
Run:   uv run --package agent-harness python examples/compaction_demo.py
"""
from __future__ import annotations

import json

from dotenv import load_dotenv

from agent_harness import Agent, AgentWorkspace, CompactionConfig
from agent_harness.context.compaction import estimate_tokens
from agent_harness.messages import assistant_msg, tool_msg, user_msg
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult

load_dotenv()

MODEL = 'qwen/qwen3.7-max'
TICKERS = [f'T{n:03d}' for n in range(150)]
WINDOW = 30_000

SYSTEM = '''
<role>
You are an equity data assistant. Quote lookups go through fetch_quote.
</role>
'''

TASK = (
    'Fetch the quote for T150. Then tell me the 17th value (index 16) of `closes_30d` in the '
    'T005 quote you fetched earlier, exactly as recorded. Give both answers in one short final message.'
)


#     ================================
# --> Helper funcs
#     ================================


def quote_payload(ticker: str) -> str:
    """A deterministic, bulky quote record: 30 daily closes plus an audit code."""
    n = int(ticker[1:])
    closes = [round(100 + n * 7.31 + day * 0.42, 2) for day in range(30)]

    return json.dumps({
        'ticker': ticker,
        'price': closes[-1],
        'audit_code': f'AUD-{n * 7919 % 100_000:05d}-{ticker}',
        'closes_30d': closes,
        'note': 'Synthetic data for the compaction demo.',
    })


def preloaded_history() -> list[dict]:
    """The earlier part of a job: one task, then a fetch_quote round trip per ticker."""
    messages = [user_msg('Fetch quotes for every ticker in the watchlist, one at a time.')]

    for ticker in TICKERS:
        call = {'id': f'call_{ticker}', 'type': 'function',
                'function': {'name': 'fetch_quote', 'arguments': json.dumps({'ticker': ticker})}}

        messages += [assistant_msg('', [call]), tool_msg(call['id'], quote_payload(ticker))]

    messages.append(assistant_msg(f'Fetched {len(TICKERS)} quotes so far.'))

    return messages


@agent_tool
def fetch_quote(ticker: str) -> ToolResult:
    """Fetch the latest quote record for one ticker."""
    return ToolResult(quote_payload(ticker), status='ok')


def print_history(agent: Agent) -> None:
    """One line per message: index, role, and the start of its text."""
    print('\n=== agent.messages after the run ===')

    for i, message in enumerate(agent.messages):
        content = message.get('content')
        text = content if isinstance(content, str) else (content[0]['text'] if content else '')
        calls = [c['function']['name'] for c in message.get('tool_calls') or []]
        preview = ' '.join(text.split())[:90] or f'-> {calls}'

        print(f'[{i:>2}] {message["role"]:<9} {preview}')


#     ================================
# --> Run
#     ================================


def main() -> None:
    agent = Agent(
        model=MODEL,
        system=SYSTEM,
        tools=[fetch_quote],
        compaction=CompactionConfig(context_window=WINDOW),
    )

    AgentWorkspace('compaction-demo').attach(agent)

    # Preload after attach: attach extends the system prompt, which needs an empty history
    agent.messages.extend(preloaded_history())

    print(f'Preloaded {len(agent.messages)} messages, ~{estimate_tokens(agent.messages):,} estimated tokens; '
          f'window {WINDOW:,}, compacts at {int(WINDOW * 0.8):,}.\n')

    result = agent.run(TASK)

    print_history(agent)
    print(f'\n=== final answer ===\n{result}')
    print(f'\nExpected T005 closes_30d[16]: {json.loads(quote_payload("T005"))["closes_30d"][16]}')


if __name__ == '__main__':
    main()
