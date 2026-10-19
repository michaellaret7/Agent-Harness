"""The CIO: the fund's lead agent. It sets the sector budget for a stock-and-ETF portfolio and receives every kickoff message.

    ~/.agent_harness/
    ├── workspaces/fund-cio/sandbox/      ExecuteCode working dir (files persist across runs)
    │   └── skills/curated/  ◄── ./skills/ (re-copied on every attach)
    │       skills/learned/  ◄── the CIO's own
    └── memory/fund-cio.sqlite            long-term memory: Remember / Recall / Forget

Every run ends with a validated `SectorAllocation` (see `output.py`), left on `cio.submission`.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agent_harness import Agent, AgentWorkspace, CompactionConfig

from fund.agents.sandbox import FUND_SANDBOX

ROLE = 'CIO'
MODEL = 'openai/gpt-6-astra'
SYSTEM_PROMPT = (Path(__file__).parent / 'prompt.md').read_text(encoding='utf-8').strip()
CURATED_SKILLS = Path(__file__).parent / 'skills'

workspace = AgentWorkspace(
    'fund-cio',
    curated_skills=CURATED_SKILLS,
    sandbox=replace(FUND_SANDBOX, recursive_agents=True),
    memory=True,
    auto_memory_recall=False,
    curate_memory=True,
)

# One CIO per process: registered once, then run for every message it receives
cio = Agent(
    system=SYSTEM_PROMPT,
    model=MODEL,
    max_iters=200,
    reasoning_effort='high',
    compaction=CompactionConfig(context_window=272_000)
)

workspace.attach(cio)
