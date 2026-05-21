"""Entry point for `python -m coding`. Launches the TUI with the coding-domain Agent."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so `agent.*`, `tools.*`, `tui.*` resolve
# when this module is run via `python -m coding` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import Agent
from coding.tools.bash import bash
from coding.tools.edit import edit
from coding.tools.glob import glob
from coding.tools.grep import grep
from agent.base_tools.read import read
from coding.tools.tree import tree
from coding.tools.write import write
from tui.app import TUIApp


def main() -> None:
    here = Path(__file__).parent

    agent = Agent(
        provider='openrouter',
        model='anthropic/claude-opus-4.7',
        tools=[bash, read, write, edit, glob, grep, tree],
        prompt=(here / 'context' / 'prompt.md').read_text(encoding='utf-8').strip(),
        skills_dir=here / 'skills',
        memory_path=here / 'context' / 'memory.md',
    )

    app = TUIApp(agent)

    asyncio.run(app.run_async())


if __name__ == '__main__':
    main()
