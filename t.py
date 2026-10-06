"""Scratch script: agents in an organization messaging each other over a bus."""

from dotenv import load_dotenv

from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from architectures import Message, Organization

load_dotenv()


def make_agent() -> Agent:
    return Agent(
        system="<role>You are a helpful assistant in a multi-agent organization. Use SendMessage to talk to colleagues.</role>",
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
a_id = org.register_agent("agent_a", make_agent())
b_id = org.register_agent("agent_b", make_agent())

# Kick off: the org (an outside sender) asks agent_a to consult agent_b
org.post(Message(
    sender_id=org.id,
    recipient_id=a_id,
    content="Ask agent_b what our org's AUM and sector are. When agent_b replies, just acknowledge it — don't message again.",
))

org.run_until_idle()
