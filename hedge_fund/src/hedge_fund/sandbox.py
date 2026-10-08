"""The fund's code sandbox: one ExecuteCode tool wired to the brokerage and market-data credentials.

    Agent ── ExecuteCode ─► sandbox kernel
                            ├─ alpaca-py  (account, positions, paper orders)
                            └─ fmpsdk     (fundamentals, market data)

The sandbox runs on the host interpreter, so it imports every library in the
hedge-fund package's dependencies. Only the FMP and Alpaca credentials are
passed into the kernel.
"""
from __future__ import annotations

import os
from typing import Any

from agent_harness.base_tools.code_execution.tool import execute_code_tool

# Host credentials the sandbox kernel may read; everything else in `.env` stays on the host.
SANDBOX_CREDENTIALS = ('FMP_API_KEY', 'APCA_API_KEY_ID', 'APCA_API_SECRET_KEY')


def fund_sandbox() -> dict[str, Any]:
    """Build a fresh ExecuteCode tool with its own sandbox, screened before every call.

    Fails fast with a KeyError if any sandbox credential is missing from the environment.
    """
    env = {name: os.environ[name] for name in SANDBOX_CREDENTIALS}

    return execute_code_tool(env=env, j_screen=True)
