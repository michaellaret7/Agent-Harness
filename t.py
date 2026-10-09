"""Scratch script: an agent with an output model (validated termination).

The agent can only finish by calling SubmitResult with a valid `StockReport`.
"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool

load_dotenv()


#     ================================
# --> Output model
#     ================================


class StockQuote(BaseModel):
    ticker: str
    price: float = Field(description='Latest price in USD.')
    pe_ratio: float | None = Field(description='Trailing P/E, or null if not available.')


class StockReport(BaseModel):
    quotes: list[StockQuote]
    cheapest_by_pe: str = Field(description='Ticker with the lowest P/E among the quotes.')
    evidence: str = Field(description='Which API calls you made and what they returned.')


#     ================================
# --> Run
#     ================================


agent = Agent(
    system='<role>You are a market data analyst. Use ExecuteCode with the FMP API for all numbers.</role>',
    provider='openrouter',
    model='openai/gpt-6-sol',
    tools=[execute_code_tool(env={'FMP_API_KEY': os.environ['FMP_API_KEY']}, packages=['pandas'])],
    # output_model=StockReport,
)

report = agent.run('Get the latest price and trailing P/E for AAPL, MSFT and NVDA, and tell me which is cheapest by P/E.')
# assert isinstance(report, StockReport)   # with output_model set, run() returns the validated object

print(report)