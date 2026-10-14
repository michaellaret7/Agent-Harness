"""Scratch script: a host agent fans out concurrent sub-agents from inside ExecuteCode.

    host Agent (depth 0)
      └─ ExecuteCode kernel (depth 1)           one call, a ThreadPoolExecutor
           ├─ sub-agent AAPL ─┐
           ├─ sub-agent MSFT ─┼─ run concurrently, each with its own FMP tool
           ├─ sub-agent NVDA ─┤
           └─ sub-agent GOOGL ┘
      └─ host compares the four summaries

`SandboxConfig(recursive_agents=True)` passes the host's OpenRouter key into the
kernel and makes the ExecuteCode prompt teach the sub-agent pattern. Sub-agents built at depth 1
cannot build agents of their own.
"""

import os
import time

from dotenv import load_dotenv

from agent_harness import Agent
from agent_harness.base_tools.code_execution.config import SandboxConfig
from agent_harness.base_tools.code_execution.tool import execute_code_tool

load_dotenv()

MODEL = 'openai/gpt-6-sol'
TICKERS = ['AAPL', 'MSFT', 'NVDA', 'GOOGL']

TASK = f"""Compare the last 3 fiscal years of revenue growth, free cash flow margin and net debt for {', '.join(TICKERS)}.

Do the per-ticker work with concurrent sub-agents, all in ONE ExecuteCode call:
- Define an @agent_tool `fmp_get(path: str, params: dict) -> ToolResult` that calls
  https://financialmodelingprep.com/stable/<path> with os.environ['FMP_API_KEY'] and returns the JSON text.
- Build one sub-agent per ticker (provider='openrouter', model='{MODEL}', tools=[fmp_get]) whose task is
  to fetch that ticker's statements and return a short plain-text summary of the three metrics.
- Run them with concurrent.futures.ThreadPoolExecutor, passing sink=BaseSink() (from agent_harness.sinks.base)
  to each run so only the summaries print. Time the whole fan-out and print the elapsed seconds.
- Pass timeout=900 to ExecuteCode.

Then compare the four companies in one table and end with a one-line verdict."""

sandbox = SandboxConfig(
    env={'FMP_API_KEY': os.environ['FMP_API_KEY']},
    recursive_agents=True,
)

execute_code = execute_code_tool(sandbox)

agent = Agent(
    system=(
        '<role>\n'
        'You are a lead equity analyst who delegates per-company research to sub-agents and synthesizes '
        'their findings. Every number must come from FMP.\n'
        '</role>'
    ),
    tools=[execute_code],
    provider='openrouter',
    model=MODEL,
)

started = time.perf_counter()
answer = agent.run(TASK)

print('\n=== Final answer ===')
print(answer)
print(f'\nTotal run time: {time.perf_counter() - started:.0f}s')
