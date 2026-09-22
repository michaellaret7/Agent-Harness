"""Headless development entry point for `python -m agent_harness`.

Exercises the streaming loop and built-in tools without the optional TUI
library. Applications consume Agent directly from their own projects.
"""
from __future__ import annotations

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


def main() -> None:
    # Application owns config bootstrap: load .env before constructing the
    # Agent so `agent_harness/` (which only reads the environment) sees credentials.
    load_dotenv()

    agent = Agent(
        provider='openrouter', 
        model='qwen/qwen3.7-max'
    )

    while True:
        x = input("Enter a task: ")
        agent.run(x)


if __name__ == '__main__':
    main()
