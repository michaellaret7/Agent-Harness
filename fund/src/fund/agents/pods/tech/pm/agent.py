"""The Technology PM: leads the tech pod. It turns analyst ideas into sized trades inside the CIO's capital budget.

    ~/.agent_harness/
    ├── workspaces/fund-tech-pm/sandbox/  ExecuteCode working dir (files persist across runs)
    │   └── skills/learned/  ◄── the PM's own
    └── memory/fund-tech-pm.sqlite        long-term memory: theses, decisions, lessons

Each analyst message (new idea, position update, watchlist name) wakes the PM, which decides
whether to trade, hold, add, trim, or exit, executes it in the Alpaca paper account
(`fund.tools`), and replies with the decision.
"""
from __future__ import annotations

from pathlib import Path

from agent_harness import Agent, AgentWorkspace, CompactionConfig

from fund.agents.sandbox import FUND_SANDBOX
from fund.tools import paper_client, trading_tools

ROLE = 'Technology PM'
MODEL = 'openai/gpt-6-astra'
SYSTEM_PROMPT = (Path(__file__).parent / 'prompt.md').read_text(encoding='utf-8').strip()

# Auto recall: every incoming idea surfaces the PM's past theses and decisions on the same names
workspace = AgentWorkspace(
    'tech-pm',
    sandbox=FUND_SANDBOX,
    memory=True,
    auto_memory_recall=False,
    curate_memory=True,
)

# One PM per process: registered once, then run for every message it receives
pm = Agent(
    system=SYSTEM_PROMPT,
    model=MODEL,
    tools=trading_tools(paper_client()),
    max_iters=200,
    reasoning_effort='high',
    compaction=CompactionConfig(context_window=272_000)
)

workspace.attach(pm)
