"""Run an agent that returns a Pydantic model.

Run: python t.py
"""
from __future__ import annotations

from dotenv import load_dotenv
from pydantic import BaseModel

from agent_harness.agent import Agent
from agent_harness.sinks import LogSink

load_dotenv()

MODEL = 'openai/gpt-5.6-luna'


class ResearchResult(BaseModel):
    topic: str
    summary: str
    key_points: list[str]


agent = Agent(
    provider='openrouter',
    model=MODEL,
    system='Give concise, factual answers.',
    output_model=ResearchResult,
)


if __name__ == '__main__':
    result = agent.run(
        task='Briefly explain how solar panels generate electricity after doing online research.',
        sink=LogSink('research'),
    )

    print(result)