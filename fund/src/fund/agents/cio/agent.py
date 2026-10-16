"""The CIO: the fund's lead agent. It sets the sector budget for a stock-and-ETF portfolio and receives every kickoff message.

    ~/.agent_harness/
    ├── workspaces/fund-cio/sandbox/      ExecuteCode working dir (files persist across runs)
    │   └── skills/curated/  ◄── ./skills/ (re-copied on every attach)
    │       skills/learned/  ◄── the CIO's own
    └── memory/fund-cio.sqlite            long-term memory: Remember / Recall / Forget

Every run ends with a validated `SectorAllocation` (see `output.py`), left on `cio.submission`.
"""
from __future__ import annotations

import os
from pathlib import Path

from agent_harness import Agent, AgentWorkspace, CompactionConfig, SandboxConfig

from fund.agents.cio.output import SectorAllocation, check_allocation

ROLE = 'CIO'
MODEL = 'openai/gpt-6-astra'
SYSTEM_PROMPT = (Path(__file__).parent / 'prompt.md').read_text(encoding='utf-8').strip()

# Workspace config: host credentials the sandbox kernel may read (everything else in `.env` stays on the host)
WORKSPACE_NAME = 'fund-cio'
SANDBOX_CREDENTIALS = ('FMP_API_KEY',)
SANDBOX_PACKAGES = ('pandas', 'numpy', 'pyarrow')
CURATED_SKILLS = Path(__file__).parent / 'skills'

# Exhaustive research has no time limit: the iteration cap is only a runaway guard
MAX_ITERS = 1000
REASONING_EFFORT = 'high'

# Compact before the prompt crosses the model's 272k-token price break (input cost doubles above it);
# research notes live in sandbox files, so compaction loses no findings
COMPACTION = CompactionConfig(context_window=272_000)


# Fails fast with a KeyError at import if any sandbox credential is missing: load `.env` first
workspace = AgentWorkspace(
    WORKSPACE_NAME,
    curated_skills=CURATED_SKILLS,
    sandbox=SandboxConfig(
        env={name: os.environ[name] for name in SANDBOX_CREDENTIALS},
        packages=SANDBOX_PACKAGES,
        j_screen=True,
        recursive_agents=True,
    ),
    memory=True,
    auto_memory_recall=True,
    curate_memory=True,
)

# One CIO per process: registered once, then run for every message it receives
cio = Agent(
    system=SYSTEM_PROMPT,
    model=MODEL,
    max_iters=MAX_ITERS,
    reasoning_effort=REASONING_EFFORT,
    compaction=COMPACTION,
    output_model=SectorAllocation,
    verifier=check_allocation,
)

workspace.attach(cio)
