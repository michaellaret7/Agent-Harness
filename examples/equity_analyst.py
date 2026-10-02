"""Equity analyst agent: custom FMP data tools + a Python sandbox over one shared workspace.

The two custom tools fetch from Financial Modeling Prep and save the full
result as CSV into the agent's workspace, returning a compact preview of the
key figures. ExecuteCode runs in that same workspace, so the agent pulls data
with a tool and analyses it with pandas without re-typing numbers.

    financial_statements ─┐
    price_history ────────┼─► <workspace>/*.csv ─► ExecuteCode (pandas)
                          │
    WebSearch / WebExtract (base tools) for context outside FMP

`make_analyst(model)` builds a fresh agent with its own workspace; evals call
it once per case.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated

import httpx
import pandas as pd

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from agent_harness.tooling.decorator import Param, agent_tool, bind_tool
from agent_harness.tooling.result import ToolResult

FMP_BASE = 'https://financialmodelingprep.com/stable/'

STATEMENT_PATHS = {
    'income': 'income-statement',
    'balance': 'balance-sheet-statement',
    'cash_flow': 'cash-flow-statement',
}

# Columns shown in the tool's preview; the CSV keeps every column FMP returns.
PREVIEW_COLUMNS = {
    'income': ['revenue', 'grossProfit', 'operatingExpenses', 'operatingIncome', 'netIncome', 'epsDiluted'],
    'balance': ['totalAssets', 'totalLiabilities', 'totalStockholdersEquity', 'cashAndCashEquivalents', 'totalDebt'],
    'cash_flow': ['operatingCashFlow', 'capitalExpenditure', 'freeCashFlow', 'netIncome', 'stockBasedCompensation'],
}

ROLE = '''<role>
You are an equity research analyst. You answer with numbers you computed from data you fetched, never from memory.
</role>

<methodology>
1. Fetch data with financial_statements and price_history. Each saves a CSV into your working directory and tells you its file name.
2. Do every calculation in ExecuteCode with pandas, reading those CSV files. Print the intermediate table you base a conclusion on.
3. Use WebSearch only for context the data tools cannot give: guidance, events, explanations.
4. State fiscal periods explicitly (company fiscal years differ from calendar years).
</methodology>

<constraints>
- Every figure in your answer must appear in a tool result or in your own printed code output.
- If something cannot be known from available data, say so plainly instead of estimating it as fact.
- Questions about periods that have not been reported yet: your first sentence says the figure is not known yet. Then give the latest reported figure as an anchor. Only after that, offer sourced guidance or estimates, each attributed, never as a single headline number.
</constraints>

<caveats>
Before answering, check each of these and state the ones that apply in one line each:
- Fiscal calendar: when a company's fiscal year differs from the calendar year, say so and name its fiscal year-end month.
- Definitions: when a metric has more than one common definition (free cash flow, margins, ROE inputs), state the one you used, and whether it was computed or taken as reported.
- Price adjustments: for price-based analysis, state whether prices are adjusted for splits and dividends, and name any split inside the period.
- Drivers: when a metric looks unusual or one factor dominates (very high leverage, a step change), name the business reason behind it if the data or a search supports one.
- Comparisons: when asked to compare, compare on every dimension the question names, not only the first.
</caveats>

<output_format>
Lead with the answer. Then a compact table of the figures behind it. Then at most a few sentences of interpretation.
</output_format>'''


#     ================================
# --> Helper funcs
#     ================================


def _fmp_get(path: str, params: dict[str, str | int]) -> list[dict]:
    """GET one FMP stable endpoint; raise on HTTP errors or an error payload."""
    response = httpx.get(FMP_BASE + path, params={**params, 'apikey': os.environ['FMP_API_KEY']}, timeout=30)
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(f'FMP returned {str(data)[:200]}')

    return data


def _preview(frame: pd.DataFrame, columns: list[str]) -> str:
    """Key columns, in billions where large, as a fixed-width table."""
    shown = frame[[c for c in columns if c in frame.columns]].copy()

    for column in shown.columns:
        if pd.Series(shown[column]).abs().max() >= 1e6:
            shown[column] = (shown[column] / 1e9).round(3).astype(str) + 'B'

    return shown.to_string()


#     ================================
# --> Tools
#     ================================


@agent_tool(safe_parallel=True)
def financial_statements(
    symbol: Annotated[str, Param(description='Ticker, e.g. NVDA.')],
    statement: Annotated[str, Param(description='Which statement.', enum=['income', 'balance', 'cash_flow'])],
    period: Annotated[str, Param(description='Annual or quarterly rows.', enum=['annual', 'quarter'])] = 'annual',
    limit: Annotated[int, Param(description='Most recent N periods.', min_val=1, max_val=20)] = 4,
    _workspace: Path = None,  # type: ignore[assignment] injected via bind_tool
) -> ToolResult:
    """Fetch income, balance-sheet or cash-flow statements from FMP. Saves every column to a CSV in your working directory and returns a preview of the key line items."""
    params: dict[str, str | int] = {'symbol': symbol.upper(), 'limit': limit}

    if period == 'quarter':
        params['period'] = 'quarter'

    rows = _fmp_get(STATEMENT_PATHS[statement], params)

    if not rows:
        return ToolResult(f'No {statement} data for {symbol}.', status='error')

    frame = pd.DataFrame(rows).set_index(['date', 'fiscalYear', 'period'])
    filename = f'{symbol.upper()}_{statement}_{period}.csv'
    frame.to_csv(_workspace / filename)

    header = f'Saved {len(frame)} rows x {len(frame.columns)} columns to {filename} (values in USD).'

    return ToolResult(f'{header}\n{_preview(frame, PREVIEW_COLUMNS[statement])}', status='ok')


@agent_tool(safe_parallel=True)
def price_history(
    symbol: Annotated[str, Param(description='Ticker, e.g. NVDA.')],
    start: Annotated[str, Param(description='First date, YYYY-MM-DD.')],
    end: Annotated[str, Param(description='Last date, YYYY-MM-DD.')],
    _workspace: Path = None,  # type: ignore[assignment] injected via bind_tool
) -> ToolResult:
    """Fetch daily end-of-day prices (split-adjusted) from FMP. Saves date, open, high, low, close, volume to a CSV in your working directory and returns a summary."""
    rows = _fmp_get('historical-price-eod/full', {'symbol': symbol.upper(), 'from': start, 'to': end})

    if not rows:
        return ToolResult(f'No prices for {symbol} between {start} and {end}.', status='error')

    frame = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume']).sort_values(by='date')
    filename = f'{symbol.upper()}_prices_{start}_{end}.csv'
    frame.to_csv(_workspace / filename, index=False)

    low, high = frame.loc[frame['close'].idxmin()], frame.loc[frame['close'].idxmax()]
    summary = (
        f'Saved {len(frame)} daily rows to {filename}.\n'
        f'range {frame["date"].iloc[0]} .. {frame["date"].iloc[-1]}\n'
        f'first close {frame["close"].iloc[0]}  last close {frame["close"].iloc[-1]}\n'
        f'min close {low["close"]} on {low["date"]}  max close {high["close"]} on {high["date"]}'
    )

    return ToolResult(summary, status='ok')


#     ================================
# --> Factory
#     ================================


def make_analyst(model: str, max_iters: int = 30) -> Agent:
    """A fresh analyst with its own workspace shared by the data tools and the sandbox."""
    workspace = Path(tempfile.mkdtemp(prefix='analyst_'))

    return Agent(
        model=model,
        system=ROLE,
        max_iters=max_iters,
        tools=[
            execute_code_tool(workspace=workspace, packages=['pandas', 'numpy']),
            bind_tool(financial_statements, _workspace=workspace),
            bind_tool(price_history, _workspace=workspace),
        ],
    )
