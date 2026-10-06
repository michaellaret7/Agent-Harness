"""Scratch script: a real traced run with live stock-basket quotes as dynamic context.

A context provider fetches FMP batch quotes on every model request, so each
iteration's generation in Langfuse ends with a fresh <market_quotes> block
inside <dynamic_context>. LangfuseSink auto-registers from LANGFUSE_PUBLIC_KEY.
"""

import os
from datetime import datetime

import httpx
from dotenv import load_dotenv

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool

load_dotenv()

BASKETS: dict[str, list[str]] = {
    'mega_cap_tech': ['AAPL', 'MSFT', 'NVDA', 'GOOGL'],
    'semiconductors': ['AMD', 'AVGO', 'TSM', 'MU'],
    'banks': ['JPM', 'BAC', 'GS', 'MS'],
}

QUOTE_URL = 'https://financialmodelingprep.com/stable/batch-quote'

#     ================================
# --> Helper funcs
#     ================================


def fetch_quotes(symbols: list[str]) -> dict[str, dict]:
    """One FMP batch call for every symbol; returns quotes keyed by symbol."""
    response = httpx.get(
        QUOTE_URL,
        params={'symbols': ','.join(symbols), 'apikey': os.environ['FMP_API_KEY']},
        timeout=15,
    )
    response.raise_for_status()

    return {quote['symbol']: quote for quote in response.json()}


def market_quotes_context() -> str | None:
    """Context provider: live quotes per basket, re-fetched on every model request."""
    symbols = [symbol for basket in BASKETS.values() for symbol in basket]

    # A network failure shows up in context instead of killing the run
    try:
        quotes = fetch_quotes(symbols)

    except httpx.HTTPError as e:
        return f'<market_quotes>\nunavailable: {e}\n</market_quotes>'

    lines = [f'as_of: {datetime.now().strftime("%H:%M:%S")}']

    for basket, members in BASKETS.items():
        lines.append(f'{basket}:')

        for symbol in members:
            quote = quotes.get(symbol)

            if quote is None:
                lines.append(f'  {symbol}  n/a')
                continue

            lines.append(f'  {symbol}  {quote["price"]:.2f}  {quote["changePercentage"]:+.2f}%')

    return '<market_quotes>\n' + '\n'.join(lines) + '\n</market_quotes>'


#     ================================
# --> Run
#     ================================

TASK = (
    'Live quotes for three stock baskets appear in <market_quotes> inside the '
    '<dynamic_context> block at the end of every request; it refreshes each time you are called. '
    'Take 3 readings: for each reading, copy the current prices from <market_quotes> into '
    'ExecuteCode and store them, then call ExecuteCode with time.sleep(20) before the next reading. '
    'After the third reading, use ExecuteCode to compute each basket\'s equal-weighted % move '
    'between the first and third reading, and report which basket moved most.'
)

agent = Agent(
    system='<role>You are a market analyst who works from the live quotes in your context.</role>',
    provider='openrouter',
    model='qwen/qwen3.7-max',
    tools=[execute_code_tool()],
    dynamic_context_providers=[market_quotes_context],
)

result = agent.run(TASK)

print('\n\n=== FINAL ===')
print(result)
