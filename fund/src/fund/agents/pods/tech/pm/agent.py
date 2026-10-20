"""The Technology PM: leads the tech pod. It turns analyst ideas into sized trades inside its capital budget.

    ~/.agent_harness/
    ├── workspaces/tech-pm/sandbox/  ExecuteCode working dir (files persist across runs)
    │   └── skills/learned/  ◄── the PM's own
    └── memory/tech-pm.sqlite        long-term memory: theses, decisions, lessons

The capital budget is `CAPITAL_BUDGET` below, rendered into the system prompt's `<capital>` block:
change it here and the next process start picks it up.

Each analyst message (new idea, position update, watchlist name) wakes the PM, which decides
whether to trade, hold, add, trim, or exit, executes it in the Alpaca paper account
(`fund.tools`), and replies with the decision.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from agent_harness import Agent, AgentWorkspace, CompactionConfig

from fund.agents.sandbox import FUND_MODEL, FUND_SANDBOX
from fund.tools import paper_client, trading_tools
from fund.tools.broker import ALPACA_CREDENTIALS

ROLE = 'PM (Technology Pod)'

CAPITAL_BLOCK = f"""<capital>
Your capital budget is 300,000: the most the sleeve's positions may be worth at market value.
Size every position as a percent of this budget, not of the account's equity or buying power.
</capital>"""

SYSTEM_PROMPT = (Path(__file__).parent / 'prompt.md').read_text(encoding='utf-8').strip() + f'\n\n{CAPITAL_BLOCK}'

# The Alpaca keys reach the sandbox so the PM's code can pull positions, orders, and account history
# for analytics on the tech sleeve with alpaca-py
PM_SANDBOX = replace(
    FUND_SANDBOX,
    env={**FUND_SANDBOX.env, **{name: os.environ[name] for name in ALPACA_CREDENTIALS}},
    packages=(*FUND_SANDBOX.packages, 'alpaca-py'),
)

# Auto recall: every incoming idea surfaces the PM's past theses and decisions on the same names
workspace = AgentWorkspace(
    'tech-pm',
    sandbox=PM_SANDBOX,
    memory=True,
    auto_memory_recall=True,
    curate_memory=True,
)

# One PM per process: registered once, then run for every message it receives
pm = Agent(
    system=SYSTEM_PROMPT,
    model=FUND_MODEL,
    tools=trading_tools(paper_client()), # register tools for the trade execution
    max_iters=200,
    reasoning_effort='high',
    # Archived inside the sandbox, so the PM can open a summary's `(archive #n)` citation with ExecuteCode
    compaction=CompactionConfig(context_window=272_000, archive_dir=workspace.sandbox_dir / 'history'),
)

# attach the workspace to the PM Agent, this also attaches the ExecuteCode tool to the PM Agent
workspace.attach(pm)