"""Subagent demo: a parent agent that deploys two subagents.

Run: python t.py

Defines two SubAgentConfigs (a poet and a translator), hands them to a parent
Agent via `subagents=`, and gives the parent a task whose only path to
completion is deploying both. The parent calls DeploySubagent(name, prompt)
for each; every deployment spins up a fresh, isolated SubAgent.
"""
from __future__ import annotations

from dotenv import load_dotenv

from agent_harness.agent import Agent
from agent_harness.base_tools.deploy_subagent import SubAgentConfig
from agent_harness.sinks import LogSink

# Application owns config bootstrap: load .env before constructing any Agent
# so agent_harness (which only reads the environment) sees credentials.
load_dotenv()

MODEL = 'cohere/north-mini-code:free'


# ---- Subagent specs ---- #

poet = SubAgentConfig(
    name='poet',
    description='Writes a haiku about a given topic. Input: the topic.',
    system='You are a poet. Given a topic, write a single haiku about it. Output only the haiku. YOU MUST USE THE WEB SEARCH TOOLS FIRST TO FIND THE BEST PRACTICES FOR YOUR TASK',
    provider='openrouter',
    model=MODEL,
)

translator = SubAgentConfig(
    name='translator',
    description='Translates English text into French. Input: the English text.',
    system='You are a translator. Translate the given English text into French. Output only the translation. YOU MUST USE THE WEB SEARCH TOOLS FIRST TO FIND THE BEST PRACTICES FOR YOUR TASK',
    provider='openrouter',
    model=MODEL,
)


# ---- Parent agent: instructed to run both subagents ---- #

parent = Agent(
    provider='openrouter',
    model=MODEL,
    system=(
        'You are a helpful assistant that can help with tasks. You will be receiving a message from the parent agent, execute the task.'
    ),
    subagents=[poet, translator],
)


if __name__ == '__main__':
    result = parent.run(
        task='Research nivida in depth and tell me whats in their research and development department. Then deploy the translator subagent to translate the information into French',
        sink=LogSink('parent'),
    )

    print('=' * 80)
    print(result)
