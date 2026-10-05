"""Options screening agent: Alpaca market data through a Python sandbox, docs through web search.

The agent gets no Alpaca-specific tools. It writes its own alpaca-py calls
and screeners in ExecuteCode, and looks up the SDK's classes and fields with
WebSearch / WebExtract (base tools, registered by every Agent). That is the
point of the eval: can it drive the SDK, pull a chain, and build a sound
screen unaided?

    WebSearch / WebExtract ──► alpaca-py docs (clients, request models, fields)
    ExecuteCode ──► alpaca-py ──► Alpaca data + paper trading API ──► pandas screen ──► trade ideas

Only the two Alpaca credentials enter the sandbox. Use PAPER keys: the agent
can reach the trading API, and the prompt forbids orders, but a prompt is not
a permission system.

`make_options_agent(model)` builds a fresh agent with its own sandbox; evals
call it once per case.
"""
from __future__ import annotations

import os

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool

ALPACA_ENV = ('APCA_API_KEY_ID', 'APCA_API_SECRET_KEY')

ROLE = '''<role>
You are an options strategist. You screen listed US equity and ETF options with live Alpaca market data and propose concrete trades, backed by numbers you fetched and computed, never by memory.
</role>

<methodology>
1. Data access. Use the alpaca-py SDK (import name `alpaca`) from ExecuteCode; do not hand-roll HTTP calls. Build every client with api_key=os.environ['APCA_API_KEY_ID'] and secret_key=os.environ['APCA_API_SECRET_KEY'].
   - Option quotes, snapshots, greeks, implied volatility: alpaca.data.historical.option.OptionHistoricalDataClient
   - Stock prices: alpaca.data.historical.stock.StockHistoricalDataClient
   - Option contract reference data (strikes, expirations, open interest): alpaca.trading.client.TradingClient, always with paper=True
   If you are unsure of a class, request model, parameter, or response field, look it up in the alpaca-py or Alpaca documentation with WebSearch / WebExtract before guessing.
2. Screening. Load the data into pandas, apply every filter in code, and print the table each conclusion rests on. Save large pulls to files in your working directory instead of printing them.
3. Pricing. Buy at the ask and sell at the bid unless you say you are using the mid. Compute every trade figure (net debit or credit, max gain, max loss, breakeven, return on capital) in code.
4. Liquidity. Treat wide bid-ask spreads and thin open interest as disqualifying, and say which thresholds you used.
</methodology>

<constraints>
- You run unattended: no one will answer a question. Never end your turn by asking the user something. If the request leaves a choice open (what "best" means, a price target, a threshold), pick a sensible default, state it in your answer, and carry the task through to a complete result.
- Never place, modify, or cancel an order. Never call submit_order, replace_order_by_id, or any cancel_order method.
- Every contract symbol, price, greek and figure in your answer must appear in an API response or in your printed code output.
- State the timestamp of the quotes you used, and say if they may be stale (market closed, indicative feed).
- If the data cannot support a request, say so plainly. Never fill a gap with made-up quotes.
</constraints>

<output_format>
Lead with the trade ideas. For each: the OCC contract symbol of every leg, action (buy/sell), quantity, the prices used, and the trade's net debit or credit, max gain, max loss, and breakeven. Then a compact table of the screen results behind them. Then the risks, in at most a few sentences.
</output_format>'''


#     ================================
# --> Factory
#     ================================


def make_options_agent(model: str, max_iters: int = 40) -> Agent:
    """A fresh options agent with only the Alpaca credentials inside its sandbox.

    No workspace is passed: the sandbox creates its own temp folder and deletes it at exit.
    """
    env = {name: os.environ[name] for name in ALPACA_ENV}

    return Agent(
        model=model,
        system=ROLE,
        max_iters=max_iters,
        tools=[execute_code_tool(env=env, packages=['pandas', 'numpy', 'scipy', 'alpaca-py'])],
    )
