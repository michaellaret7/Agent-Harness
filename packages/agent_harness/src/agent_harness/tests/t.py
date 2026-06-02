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

# tool='WebSearch' scopes the gate to that one tool. Drop it (tool=None) to
# gate every tool call instead.
agent.add_gate(block_web_search, tool='WebSearch')
# agent.add_gate(block_readfile, tool='ReadFile')


# This task pushes the model toward WebSearch — watch the gate deny it, and
# the model receive 'denied: web search is disabled by policy' as the result.
agent.run(
    task='Search the web for the latest news about AI and summarize it. Then read the file t.py and give me a one-sentence summary of what it does.',
    sink=LogSink('agent')
)
