"""Entry point for `python -m agent`. Always launches the TUI."""
from __future__ import annotations

import asyncio
import os
import sys

# Ensure project root is on sys.path so `tui.*` and `tools.*` resolve when
# this module is run via `python -m agent` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import Agent
from tui.app import TUIApp


def main() -> None:
    # agent = Agent(provider='openrouter', model='qwen/qwen3.6-27b')
    # agent = Agent(provider='openrouter', model='deepseek/deepseek-v4-pro')
    # agent = Agent(provider='openrouter', model='anthropic/claude-4.6-sonnet')
    agent = Agent(provider='openrouter', model='google/gemini-3.5-flash')
    app = TUIApp(agent)

    asyncio.run(app.run_async())


if __name__ == '__main__':
    main()
