"""Portfolio Manager: the top agent in the fund's hierarchy.

    PM Agent ── ExecuteCode ─► sandbox kernel
                               ├─ alpaca-py  (account, positions, paper orders)
                               └─ fmpsdk     (fundamentals, market data)

The sandbox runs on the host interpreter, so it imports every library in the
hedge-fund package's dependencies. Only the FMP and Alpaca credentials are
passed into the kernel.
"""
from __future__ import annotations

import os
from pathlib import Path

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool

MODEL = 'openai/gpt-6-sol'

PROMPT = (Path(__file__).parent / 'prompt.md').read_text(encoding='utf-8')

# Host credentials the sandbox kernel may read; everything else in `.env` stays on the host.
SANDBOX_CREDENTIALS = ('FMP_API_KEY', 'APCA_API_KEY_ID', 'APCA_API_SECRET_KEY')


def build_portfolio_manager(model: str = MODEL) -> Agent:
    """Build a fresh Portfolio Manager with its own sandbox.

    Fails fast with a KeyError if any sandbox credential is missing from the environment.
    """
    env = {name: os.environ[name] for name in SANDBOX_CREDENTIALS}

    return Agent(
        provider='openrouter',
        model=model,
        system=PROMPT,
        tools=[execute_code_tool(env=env, j_screen=True)],
    )
