"""Subagent demo. Run: python -m agent_harness.tests.subagent_demo

A parent registers a roster of SubAgentConfig specs at construction
(`Agent(subagents=[...])`). That alone adds the DeploySubagent tool, with
each name and description baked into the schema so the parent model knows
who it can delegate to. When the model calls it, a fresh SubAgent spins up
with isolated history, runs the handed task to completion, and its final
answer comes back as the tool result.

DeploySubagent is safe_parallel, so a parent that calls it twice in one
turn runs both subagents concurrently. Subagents stream through a
LogSink (`agent.<name>`) rather than stdout — configure_logging() below
surfaces their activity on stderr so you can watch the delegation happen.
"""
from dotenv import load_dotenv

from agent_harness.agent import Agent
from agent_harness.base_tools.deploy_subagent import SubAgentConfig
from agent_harness.sinks import configure_logging

MODEL = 'x-ai/grok-4.5'

researcher = SubAgentConfig(
    name='researcher',
    description='Searches the web and returns dense, sourced notes on a topic. Use for any fact-finding.',
    system='You are a research specialist. Return terse bullet-point notes with source URLs, no prose.',
    provider='openrouter',
    model=MODEL,
)

skeptic = SubAgentConfig(
    name='skeptic',
    description='Reviews a draft or claim and returns the three weakest points. Use before finalizing anything.',
    system='You are a ruthless reviewer. Return exactly the three weakest points of what you are given.',
    provider='openrouter',
    model=MODEL,
)

load_dotenv()
configure_logging('INFO')  # surfaces the subagents' LogSink events on stderr

agent = Agent(
    provider='openrouter',
    model=MODEL,
    system='You are an editor. Delegate research to your subagents; never search the web yourself.',
    subagents=[researcher, skeptic],
)

result = agent.run(
    task=(
        'Produce a five-sentence brief on the current state of AI agents. '
        'First deploy the researcher for facts, then deploy the skeptic on your draft, '
        'then return the improved brief.'
    ),
)

print('=' * 80)
print('final brief:')
print(result)
