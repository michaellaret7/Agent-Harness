"""Entry point for `python -m coding`. Launches the TUI with the coding-domain Agent."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so `agent.*`, `tools.*`, `tui.*` resolve
# when this module is run via `python -m coding` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from agent_harness.agent import Agent
from coding.tools.bash import bash
from coding.tools.edit import edit
from coding.tools.glob import glob
from coding.tools.grep import grep
from agent_harness.base_tools.read import read
from coding.tools.tree import tree
from coding.tools.write import write
from tui.app import TUIApp
from agent_harness.sinks import StdoutSink


def main() -> None:
    # Application owns config bootstrap: load .env before constructing the
    # Agent so `agent_harness/` (which only reads the environment) sees credentials.
    load_dotenv()

    agent = Agent(
        provider='openrouter',
        model='anthropic/claude-opus-4.8',
        tools=[
            bash,
            write,
            edit,
            glob,
            grep,
            tree,
        ],
        system=(Path(__file__).parent / 'system_prompt.md').read_text(encoding='utf-8').strip(),
        domain_root=Path(__file__).parent,
    )

    app = TUIApp(agent)

    asyncio.run(app.run_async())


if __name__ == '__main__':
    main()
