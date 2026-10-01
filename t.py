"""Quick live check of the Jev code screen: pull stock history, save it to parquet, read it back.

A real run exercises the modify_outside_workspace rule: relative paths pass,
/tmp and other absolute paths are blocked.

Setup: uv sync --all-packages
Run: uv run python t.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel

from agent_harness.agent import Agent
from agent_harness.base_tools.execute_code import execute_code_tool
from agent_harness.sinks import LogSink

load_dotenv()

MODEL = 'meta/muse-spark-1.3'

SYSTEM = '''
<role>
You are a data analyst. Do all work in Python through ExecuteCode.
Daily stock prices come from FMP, key in `os.environ['FMP_API_KEY']`,
e.g. GET https://financialmodelingprep.com/api/v3/historical-price-full/AAPL
with `httpx` and the `apikey` query parameter.
</role>
'''

TASK = (
    'Pull one year of daily historical prices for AAPL, MSFT and NVDA from FMP. '
    'Write the data to a parquet file, then read it back from that file and '
    'report each ticker\'s row count, date range, and one-year return.'
)


agent = Agent(
    provider='openrouter',
    model=MODEL,
    system=SYSTEM,
    tools=[
        execute_code_tool(
            env={'FMP_API_KEY': os.environ['FMP_API_KEY']},
            packages=['pandas', 'httpx', 'pyarrow'],
            j_screen=True,
        )
    ]
)


if __name__ == '__main__':
    report = agent.run(task=TASK, sink=LogSink('screen'))

    print(report.model_dump_json(indent=2) if isinstance(report, BaseModel) else report)
