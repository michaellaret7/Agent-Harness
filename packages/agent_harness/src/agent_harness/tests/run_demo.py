"""Plain agent run. Run: python -m agent_harness.tests.run_demo

The minimal end-to-end flow with no hooks or gates: construct an Agent
(base tools only — WebSearch, WebExtract, ReadFile, Skill, LoadTool, Plan),
call run(), and use the returned string. With no sink passed, output
streams to stdout via StdoutSink.
"""
from dotenv import load_dotenv

from agent_harness.agent import Agent

load_dotenv()

agent = Agent(
    provider='openrouter',
    model='x-ai/grok-4.5',
    system='You are a helpful assistant.',
)

result = agent.run(task='Search the web for the latest news about AI agents and give me a three-bullet summary.')

print('=' * 80)
print('run() returned:')
print(result)
