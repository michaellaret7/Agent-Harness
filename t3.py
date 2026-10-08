"""Scratch script: an agent writes and runs another agent inside its own ExecuteCode sandbox.

The sandbox kernel runs on the host's Python, so `agent_harness` is importable there.
The only thing the child needs from us is the API key, passed through `env=`.
"""

import os

from dotenv import load_dotenv

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool

load_dotenv()

MODEL = 'openai/gpt-6-sol'

SYSTEM = f"""<role>
You are a research lead. You can build and run other agents yourself, in code.
</role>

<methodology>
Inside ExecuteCode, the `agent_harness` library is installed and OPENROUTER_API_KEY is set.
In a single ExecuteCode call you can define custom tools, build an agent with them, and run it:

```python
from agent_harness import Agent
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult
from agent_harness.base_tools.code_execution.tool import execute_code_tool

@agent_tool
def my_tool(x: int) -> ToolResult:
    \"\"\"One-line description the child model sees.\"\"\"
    return ToolResult(str(x * 2))

child = Agent(system='<role>...</role>', provider='openrouter', model='{MODEL}', tools=[my_tool])
print(child.run('task for the child'))
```

To give a child code execution too, add `execute_code_tool()` to its tools. A child cannot build agents of its own.

The child's printed result is all you see, so print its final answer. A child run can take many minutes,
so pass a generous `timeout` (up to 1800) to ExecuteCode. Read the source of agent_harness if you need more detail.
</methodology>"""

TASK = """Build a child agent that has code execution (ExecuteCode) and no other custom tools. Have the child
answer, by writing and running Python itself: "Simulate 10,000 paths of a $10,000 portfolio over 20 years with
7% mean annual return and 15% volatility (seed 42). What are the median and 5th-percentile ending values?"
Report the child's answer and confirm whether the child actually used ExecuteCode."""

agent = Agent(
    system=SYSTEM,
    provider='openrouter',
    model=MODEL,
    tools=[execute_code_tool(env={
        'OPENROUTER_API_KEY': os.environ['OPENROUTER_API_KEY'],
        'OPENROUTER_API_URL': os.environ['OPENROUTER_API_URL'],
    })],  # Langfuse keys + session id are forwarded by the sandbox itself
)

print('\n\n=== FINAL ===\n', agent.run(TASK))
