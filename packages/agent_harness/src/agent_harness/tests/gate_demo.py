"""Gate demo. Run: python -m agent_harness.tests.gate_demo

A gate is an interceptor: it fires in ToolHandler._dispatch after args are
parsed but before the tool runs, and returns a GateVerdict the loop obeys —
allow / deny(reason) / rewrite(args). A hook observes, a gate decides.
Register with agent.add_gate(fn, tool=...).

Two gates are shown here:
1. `cheapen_search`  — rewrite: force every WebSearch onto the fast/cheap
   settings. Later gates and the tool itself see the rewritten args.
2. `block_extract`   — deny: WebExtract never runs; the model receives
   'denied: <reason>' as the tool result and adapts.
"""
from dotenv import load_dotenv

from agent_harness.agent import Agent
from agent_harness.gates import GateContext, GateVerdict


def cheapen_search(ctx: GateContext) -> GateVerdict:
    """Rewrite every WebSearch to basic mode with at most 3 results."""

    args = {**ctx.args, 'mode': 'basic', 'max_results': min(ctx.args.get('max_results', 5), 4)}

    print(f'[gate] rewrote WebSearch args: mode=basic, max_results={args["max_results"]}')

    return GateVerdict.rewrite(args)


def block_extract(ctx: GateContext) -> GateVerdict:
    """Deny every WebExtract call — excerpts from WebSearch must suffice."""

    print(f'[gate] denied WebExtract for {ctx.args.get("url", "?")}')

    return GateVerdict.deny('page extraction is disabled by policy; work from the search excerpts')


load_dotenv()

agent = Agent(
    model='x-ai/grok-4.5',
    system='You are a helpful research assistant.',
)

agent.add_gate(cheapen_search, tool='WebSearch')
agent.add_gate(block_extract, tool='WebExtract')

agent.run(task='Search the web for the latest news about AI agents, extract the most promising page, and summarize it.')
