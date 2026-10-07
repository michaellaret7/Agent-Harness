"""Scratch script: a portfolio manager fans research out to three colleagues who work in parallel.

Watch the timeline: the three specialists start within seconds of each other (parallel),
while their reports queue in the manager's inbox and are handled one at a time (serial).
"""

import time
import uuid

from dotenv import load_dotenv

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from agent_harness.hooks import HookContext
from architectures import Organization

load_dotenv()

START = time.monotonic()


def make_agent(role_prompt: str) -> Agent:
    return Agent(
        system=f"<role>{role_prompt} Use SendMessage to talk to colleagues.</role>",
        provider='openrouter',
        model='openai/gpt-6-sol',
        tools=[execute_code_tool()],  # own sandbox per agent, for research / screening work
    )


def log(role: str, text: str) -> None:
    print(f'[{time.monotonic() - START:6.1f}s] {role:<18} {text}', flush=True)


def watch(org: Organization, agent_id: uuid.UUID) -> None:
    """Print a timeline line whenever this member starts a message, finishes one, or sends one."""
    member = org.agents[agent_id]

    def on_send(ctx: HookContext) -> None:
        # The model writes the id, so it may be malformed; SendMessage itself reports that error
        roles = {str(agent_id): m.role for agent_id, m in org.agents.items()}
        log(member.role, f'sent -> {roles.get((ctx.args or {}).get("recipient_id", ""), "?")}')

    member.agent.add_hook('turn_start', lambda ctx: log(member.role, 'START'))
    member.agent.add_hook('turn_end', lambda ctx: log(member.role, 'DONE'))
    member.agent.add_hook('tool_start', on_send, tool='SendMessage')


org = Organization(
    name="Test Organization",
    goal="Find and research undervalued US technology stocks.",
    aum=1000000,
    sector="Technology",
)

manager_id = org.register_agent("portfolio_manager", make_agent(
    "You are the portfolio manager. You delegate research to your three colleagues and make the final call."
))
semis_id = org.register_agent("semis_analyst", make_agent(
    "You are the semiconductor analyst. When given a task, use WebSearch to gather current data, then send a "
    "report of at most 150 words back to whoever assigned it via SendMessage. Do not message anyone else."
))
software_id = org.register_agent("software_analyst", make_agent(
    "You are the software analyst. When given a task, use WebSearch to gather current data, then send a "
    "report of at most 150 words back to whoever assigned it via SendMessage. Do not message anyone else."
))
risk_id = org.register_agent("risk_officer", make_agent(
    "You are the risk officer. When given a task, use WebSearch to gather current data, then send a "
    "risk note of at most 150 words back to whoever assigned it via SendMessage. Do not message anyone else."
))

for agent_id in org.agents:
    watch(org, agent_id)

# Kick off: only the manager gets a message; it fans the work out to the other three
org.run([(
    manager_id,
    "Call GetOrgInfo, then send one task to each colleague in this turn: "
    "semis_analyst — pick the most undervalued US AI semiconductor stock (P/E, EV/Sales vs. peers); "
    "software_analyst — pick the most undervalued US AI software stock; "
    "risk_officer — the top 3 macro risks for US tech stocks over the next 6 months. "
    "Ask each to report back to you. When a report arrives, do not reply to its sender; "
    "if you are still waiting on others, end your turn without sending messages. "
    "Once you have all three, reply with a short allocation of our AUM across the two picks "
    "and do not send any more messages.",
)], max_messages=20)

# The manager's last assistant message is the final recommendation
print('\n=== Manager recommendation ===')
print(org.get_agent(manager_id).messages[-1]['content'])

print(f'\nTotal wall time: {time.monotonic() - START:.1f}s')
for member in org.agents.values():
    print(f'{member.role}: {len(member.agent.messages)} messages')
