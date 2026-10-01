"""Screen Databento options in Python and research a defined-risk trade pitch.

Setup: uv sync --all-packages
Run: uv run python t.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from typing import Literal

from pydantic import BaseModel

from agent_harness.agent import Agent
from agent_harness.base_tools.execute_code import execute_code_tool
from agent_harness.gates import GateContext, GateVerdict
from agent_harness.sinks import LogSink

load_dotenv()

MODEL = 'meta/muse-spark-1.3'

SYSTEM = '''
<role>
You are an options research analyst. Screen options and find one attractive
defined-risk trade to pitch.
</role>

<methodology>
Do all the work in Python through ExecuteCode and the tools provided:

- Options data: the `databento` library (dataset `OPRA.PILLAR`), key in
  `os.environ['DB_KEY']`.
- Underlying prices: FMP, key in `os.environ['FMP_API_KEY']`.
- Web research and docs lookups: POST https://api.parallel.ai/v1/search with
  `httpx`, header `x-api-key: os.environ['PARALLEL_API_KEY']`, JSON body
  {"objective": str, "search_queries": [str, ...]}.

Screen liquid underlyings for contracts expiring in 30 to 60 days, shortlist
the best candidates, research them, and pitch the strongest one with exact
legs, entry price, max profit/loss, breakeven, and an exit plan.
</methodology>

<constraints>
- Every number must come from an API response or a Python calculation.
- Mark the report provisional if quotes are stale; return no_trade if nothing
  qualifies. Do not place orders.
</constraints>
'''

TASK = (
    'Use the databento Python library in ExecuteCode with DB_KEY to screen '
    'attractive options expiring in 30 to 60 days. Research the strongest '
    'candidates and pitch your best defined-risk trade with '
    'exact legs, conservative pricing, computed payoff scenarios, catalysts, '
    'risks, and an exit plan. Label provisional ideas and return no trade if '
    'nothing qualifies. Do not place any orders.'
)


agent = Agent(
    provider='openrouter',
    model=MODEL,
    system=SYSTEM,
    tools=[
        execute_code_tool(
            env={
                'FMP_API_KEY': os.environ['FMP_API_KEY'],
                'PARALLEL_API_KEY': os.environ['PARALLEL_API_KEY'],
                'DB_KEY': 'db-GegmCWsdtdg3TSaGv9XUjYAYGKFjc',
            },
            packages=['databento', 'pandas', 'httpx'],
        )
    ]
)


if __name__ == '__main__':
    report = agent.run(task=TASK, sink=LogSink('screen'))

    print(report.model_dump_json(indent=2) if isinstance(report, BaseModel) else report)
