"""Dev entry point for `python -m agent_harness`. Launches the TUI with a tool-less Agent.

This is a sanity check for the base agent — verifies the streaming loop, the
TUI, and the base-tool registration (search/extract/skill/load_tool/plan) work
end-to-end without any domain tools attached. For the coding agent, use
`python -m coding`.
"""
from __future__ import annotations

import asyncio
import os
import sys

# Path fix runs BEFORE any `agent_harness.*` import — required when this file is
# launched by full path (e.g. from VS Code's Run button), since Python
# sets sys.path[0] to `agent_harness/` and `import agent_harness` would otherwise resolve
# to `agent_harness/agent.py` rather than the package. `python -m agent_harness` from the
# repo root doesn't need this; the fix is harmless in that case.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from agent_harness.agent import Agent
from agent_harness.sinks.log import LogSink
from tui.app import TUIApp


def main() -> None:
    # Application owns config bootstrap: load .env before constructing the
    # Agent so `agent_harness/` (which only reads the environment) sees credentials.
    load_dotenv()

    # Define the agent that will be used
    # Config tools, model, and provider
    # agent = Agent(provider='openrouter', model='anthropic/claude-opus-4.7')
    agent = Agent(
        provider='openrouter', 
        model='qwen/qwen3.7-max'
    )

    while True:
        x = input("Enter a task: ")
        agent.run(x)

    # Define the TUI app that will be the UI for the agent
    # app = TUIApp(agent)

    # Run the app asynchronously
    # asyncio.run(app.run_async())


if __name__ == '__main__':
    main()
