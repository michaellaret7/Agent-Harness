"""Dev entry point for `python -m agent`. Launches the TUI with a tool-less Agent.

This is a sanity check for the base agent — verifies the streaming loop, the
TUI, and the base-tool registration (search/extract/skill/load_tool/plan) work
end-to-end without any domain tools attached. For the coding agent, use
`python -m coding`.
"""
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
    agent = Agent(provider='openrouter', model='anthropic/claude-opus-4.7')
    app = TUIApp(agent)

    asyncio.run(app.run_async())


if __name__ == '__main__':
    main()
