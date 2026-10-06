"""Scratch script: a manager delegates market research to an analyst over the org bus."""

from dotenv import load_dotenv

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from architectures import Message, Organization

load_dotenv()


def make_agent(role_prompt: str) -> Agent:
    return Agent(
        system=f"<role>{role_prompt} Use SendMessage to talk to colleagues.</role>",
        provider='openrouter',
        model='qwen/qwen3.7-max',
        tools=[execute_code_tool()],  # own sandbox per agent, for research / screening work
    )


org = Organization(
    name="Test Organization",
    goal="Find and research undervalued US technology stocks.",
    aum=1000000,
    sector="Technology",
)
manager_id = org.register_agent("portfolio_manager", make_agent(
    "You are the portfolio manager. You delegate research to the research_analyst and make the final call."
))
analyst_id = org.register_agent("research_analyst", make_agent(
    "You are the research analyst. When given a research task, use WebSearch / WebExtract to gather current "
    "data, then send a concise report back to whoever assigned it via SendMessage."
))

# Kick off: the org (an outside sender) asks the manager to delegate research to the analyst
org.post(Message(
    sender_id=org.id,
    recipient_id=manager_id,
    content=(
        "Delegate a market research task to the research_analyst: the US AI semiconductor market — "
        "market size and growth, the 3-5 key public players, and which look undervalued vs. peers (P/E, EV/Sales). "
        "Ask them to report back to you. When their report arrives, reply with a short investment recommendation "
        "for our AUM and do not send any more messages."
    ),
))

org.run_until_idle()

# The manager's last assistant message is the final recommendation
print('\n=== Manager recommendation ===')
print(org.get_agent(manager_id).messages[-1]['content'])

for member in org.agents.values():
    print(f'{member.role}: {len(member.agent.messages)} messages')
