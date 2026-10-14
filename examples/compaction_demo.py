"""Live check of context compaction on a realistic long job.

The agent gets one task (fetch 60 quotes, then answer from them) and fills its own
context with tool results until compaction fires mid-run. Afterwards the history
reads [system, task (pinned), <compacted_history>, ...tail], and the final answer
needs a value that only the archive holds verbatim.

The 20k window leaves room for what compaction cannot shrink: the tool schemas
(~5.6k tokens with a workspace attached) plus the system prompt.

Setup: uv sync --all-packages
Run:   uv run --package agent-harness python examples/compaction_demo.py
"""
from __future__ import annotations

import json

from dotenv import load_dotenv

from agent_harness import Agent, AgentWorkspace, CompactionConfig
from agent_harness.sinks.base import CompactionEvent
from agent_harness.sinks.stdout import StdoutSink
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult

load_dotenv()

MODEL = 'qwen/qwen3.7-max'
WINDOW = 20_000
TICKERS = [f'T{n:03d}' for n in range(60)]

SYSTEM = '''
<role>
You are an equity data assistant. Quote lookups go through fetch_quote.
</role>
'''

TASK = (
    f'Fetch the quote for every ticker from {TICKERS[0]} to {TICKERS[-1]} with fetch_quote, '
    'ten tickers per message (ten parallel calls), in order. When all 60 are fetched, reply with: '
    '(1) the ticker with the highest price, and (2) the exact `audit_code` of T007.'
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


@agent_tool
def fetch_quote(ticker: str) -> ToolResult:
    """Fetch the latest quote record for one ticker."""
    return ToolResult(quote_payload(ticker), status='ok')


class DemoSink(StdoutSink):
    """StdoutSink that also snapshots the history shape right after each compaction."""

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self.agent = agent

    def on_compaction(self, event: CompactionEvent) -> None:
        super().on_compaction(event)
        print_history(self.agent, title=f'history right after compaction ({len(self.agent.messages)} messages)')


def print_history(agent: Agent, title: str) -> None:
    """One line per message: index, role, and the start of its text."""
    print(f'\n=== {title} ===')

    for i, message in enumerate(agent.messages):
        content = message.get('content')
        text = content if isinstance(content, str) else (content[0]['text'] if content else '')
        calls = [json.loads(c['function']['arguments']).get('ticker', c['function']['name'])
                 for c in message.get('tool_calls') or []]
        preview = ' '.join(text.split())[:90] or f'-> fetch_quote {calls}'
        pinned = '   ← turn_task (pinned)' if message is agent.turn_task else ''

        print(f'[{i:>2}] {message["role"]:<9} {preview}{pinned}')

    print()


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

    print(f'Window {WINDOW:,} tokens, compacts at {int(WINDOW * 0.8):,}.\n')

    result = agent.run(TASK, sink=DemoSink(agent))

    print_history(agent, title='history at the end of the run')
    print(f'=== final answer ===\n{result}')
    print(f'\nExpected: {TICKERS[-1]} is highest; T007 audit_code {json.loads(quote_payload("T007"))["audit_code"]}')


if __name__ == '__main__':
    main()
