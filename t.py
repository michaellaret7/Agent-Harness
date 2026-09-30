"""Screen stocks with FMP inside the sandbox, then research the shortlist with Parallel.

Run: python t.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel

from agent_harness.agent import Agent
from agent_harness.base_tools.execute_code import execute_code_tool
from agent_harness.gates import GateContext, GateVerdict
from agent_harness.sinks import LogSink

load_dotenv()

MODEL = 'meta/muse-spark-1.3'

SYSTEM = '''
<role>
You are an equity research analyst. You screen candidates quantitatively with
code, then research each survivor qualitatively before forming a view.
</role>

<methodology>
## 0. Find the documentation yourself
The `WebSearch` tool is off limits and will be denied. All web research goes
through the Parallel API, called with `httpx` from inside ExecuteCode. The
key is in `os.environ['PARALLEL_API_KEY']`.

- POST https://api.parallel.ai/v1/search with header `x-api-key` and JSON
  body {"objective": str, "search_queries": [str, ...], "mode": "basic"}.
  Each result has title, url and excerpts.
- POST https://api.parallel.ai/v1/extract with the same header and body
  {"urls": [str, ...], "objective": str} to read a page in full.

Write a small `search(objective, queries)` helper once and reuse it. You do
not know the Financial Modeling Prep endpoints or field names; discover them
through Parallel before screening. The FMP key is in
`os.environ['FMP_API_KEY']`.

## 1. Screen with ExecuteCode
Pull the screener universe once, store it in a variable, then fetch
per-symbol metrics only for the symbols still in contention. Compute medians
and ranks in code and print a compact table of the finalists with the
metrics you used.

## 2. Shortlist
Pick 3 to 5 tickers. Each needs a one-line quantitative reason drawn from the
table you printed.

## 3. Research
For each ticker, search through Parallel for recent results, guidance,
competitive position and risks; extract the page when an excerpt is
truncated or you need the primary filing. Prefer primary sources (earnings
releases, SEC filings) over commentary.
</methodology>

<constraints>
- Never call the WebSearch tool. Use the Parallel API in code instead.
- Every factual claim in research_summary and key_risks must trace to a URL
  in that ticker's sources.
- Do not fetch per-symbol metrics for the whole universe; narrow first.
- Report the screen exactly as run: criteria, thresholds and how many
  companies were considered.
</constraints>
'''

TASK = (
    'Screen US Technology-sector companies with market cap between $10B and $200B '
    'for quality at a reasonable price: high return on invested capital, positive free '
    'cash flow, and a P/E below the sector median. Shortlist 3 to 5, research each, '
    'and report.'
)


class StockResearch(BaseModel):
    ticker: str
    company: str
    screen_rationale: str
    research_summary: str
    key_risks: list[str]
    sources: list[str]


class ScreenReport(BaseModel):
    screen_criteria: str
    candidates_screened: int
    shortlist: list[StockResearch]


agent = Agent(
    provider='openrouter',
    model=MODEL,
    system=SYSTEM,
    tools=[
        execute_code_tool(env={
            'FMP_API_KEY': os.environ['FMP_API_KEY'],
            'PARALLEL_API_KEY': os.environ['PARALLEL_API_KEY'],
        })
    ],
    output_model=ScreenReport,
)


def deny_web_search(ctx: GateContext) -> GateVerdict:
    """Force all searching through the Parallel API inside the sandbox."""
    return GateVerdict.deny('WebSearch is disabled for this agent. Call the Parallel API with httpx inside ExecuteCode instead.')


agent.add_gate(deny_web_search, tool='WebSearch')


if __name__ == '__main__':
    report = agent.run(task=TASK, sink=LogSink('screen'))

    print(report.model_dump_json(indent=2) if isinstance(report, BaseModel) else report)
