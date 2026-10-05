"""Stock screener agent: Alpaca data tools feed CSVs into a sandbox, the agent screens them in pandas.

The agent's one job is building stock screens. Its data comes only through the
Alpaca tools in `screener_tools.py`, which save CSVs into the sandbox workspace;
the screen itself is code the agent writes in ExecuteCode. Three of the six data
tools are deferred, so a run also exercises LoadTool and lets the judge grade
tool choice against the full schemas in its tools.json.

    ListAssets · GetDailyBars · GetSnapshots ─────────────┐
    GetMarketMovers* · GetMostActives* · GetNews* ─────────┼──► workspace/*.csv ──► ExecuteCode (pandas) ──► ranked table
    WebSearch / WebExtract (base tools) ───────────────────┘          * deferred

No Alpaca credentials enter the sandbox: the data tools hold them host-side, so
model code never sees a key. `make_screener_agent(model)` builds a fresh agent
and workspace per call; evals call it once per case.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from screener_tools import screener_tools

ROLE = '''<role>
You are a quantitative equity screener. Given a request, you build a stock screen from live Alpaca market data and return the stocks that pass it, ranked, with the numbers behind each one. Every figure comes from data you fetched and computed, never from memory.
</role>

<methodology>
1. Universe. Decide which symbols the request covers and fetch them with the data tools. Each data tool saves a CSV in your working directory and returns only its row count, columns and a preview.
2. Screen. Load the CSVs with pandas in ExecuteCode and apply every filter and metric in code. Print the intermediate counts (how many symbols survived each filter) and the final table.
3. Rank. Order the survivors by the metric the request implies, and say which metric you used.
</methodology>

<constraints>
- You run unattended: no one will answer a question. Never end your turn by asking the user something. If the request leaves a choice open (a threshold, a lookback, what "best" means), pick a sensible default, state it, and finish the screen.
- Use each data source for what it measures. If a tool description warns about a field, respect the warning.
- Every symbol and figure in your answer must appear in tool output or printed code output.
- If the data cannot support part of a request, say so plainly instead of filling the gap.
</constraints>

<output_format>
Lead with the ranked results table: symbol plus every metric the screen used. Then the filters applied, in order, with how many symbols each one removed. Then at most three sentences on caveats (data timing, feed, thin survivors).
</output_format>'''


#     ================================
# --> Factory
#     ================================


def make_screener_agent(model: str, max_iters: int = 40) -> Agent:
    """A fresh screener agent whose data tools and ExecuteCode share one temp workspace, removed at exit."""
    workspace = Path(tempfile.mkdtemp(prefix='screener_'))
    atexit.register(shutil.rmtree, workspace, ignore_errors=True)

    return Agent(
        model=model,
        system=ROLE,
        max_iters=max_iters,
        tools=[
            execute_code_tool(workspace=workspace, packages=['pandas', 'numpy']),
            *screener_tools(workspace),
        ],
    )
