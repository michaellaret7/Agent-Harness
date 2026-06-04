"""Minimal gate demo. Run: python -m agent_harness.tests.t

A gate is just a function: it takes a GateContext (the tool call about to
run) and returns a GateVerdict — allow / deny(reason) / rewrite(args).
Register it with agent.add_gate(fn, tool=...). Here we block WebSearch.
"""
from dotenv import load_dotenv

from agent_harness.agent import Agent
from agent_harness.gates import GateContext, GateVerdict
from agent_harness.sinks import LogSink


def block_web_search(ctx: GateContext) -> GateVerdict:
    """Deny every WebSearch call and print that the gate fired."""

    return GateVerdict.deny('web search is disabled by policy')

def block_readfile(ctx: GateContext) -> GateVerdict:
    """Deny every ReadFile call and print that the gate fired."""

    return GateVerdict.deny('readfile is disabled by policy')

load_dotenv()

agent = Agent(
    provider='openrouter',
    model='qwen/qwen3.7-max',
    system='You are a helpful assistant that can help with coding tasks.',
)

result = agent.run(task='Search the web for the latest news about AI and summarize it and return next task to the agent to continue the research but more in depth')

print("="*250)

agent2 = Agent(
    provider='openrouter',
    model='qwen/qwen3.7-max',
    system='You are a helpful assistant that can help with tasks. You will be receiving a message from the parent agent, execute the task.',
).run(task=result)

