from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from agent.client import build_client
from agent.loop import execution_loop
from agent.messages import system_msg, user_msg
from agent.sinks import Sink
from agent.skills import Skill, format_skill_listing, load_skills
from agent.tool_handler import ToolHandler
from tools.base.bash import bash
from tools.base.edit import edit
from tools.base.extract import extract
from tools.base.glob import glob
from tools.base.grep import grep
from tools.base.read import read
from tools.base.search import search
from tools.base.skill import skill_loader
from tools.base.tree import tree
from tools.base.write import write

load_dotenv()


class Agent:
    def __init__(
        self,
        provider: str = 'vllm',
        model: str | None = None,
    ) -> None:

        self.client, self.model = build_client(provider, model)
        self.provider = provider
        self.messages: list[dict] = []

        # Tool registry
        self.tools: list[dict[str, Any]] = []
        self.tool_functions: dict[str, Callable] = {}

        self.tool_handler = ToolHandler(self)

        self.system_prompt = (Path(__file__).parent / 'context' / 'system_prompt.md').read_text(encoding='utf-8').strip()
        self.memory = (Path(__file__).parent / 'context' / 'memory.md').read_text(encoding='utf-8').strip()

        self.skills: list[Skill] = load_skills()

        # ---- Register base tools ---- #
        self.add_tool(bash)
        self.add_tool(read)
        self.add_tool(write)
        self.add_tool(edit)
        self.add_tool(glob)
        self.add_tool(grep)
        self.add_tool(tree)
        self.add_tool(search)
        self.add_tool(extract)
        self.add_tool(skill_loader(self.skills))

        self.build_initial_context()

    def add_tool(self, tool: dict[str, Any] | Callable) -> None:
        """Register a tool.

        Accepts either a tool dict (`{name, description, parameters, function}`)
        or a function decorated with `@agent_tool` (carries `.tool` attr).
        No-op if a tool with the same name is already registered.
        """
        if callable(tool) and hasattr(tool, 'tool'):
            tool = tool.tool

        name = tool['name']
        
        if name in self.tool_functions:
            return

        self.tools.append({
            'type': 'function',
            'function': {
                'name': name,
                'description': tool['description'],
                'parameters': tool['parameters'],
            },
        })

        self.tool_functions[name] = tool['function']

    def build_initial_context(self) -> None:
        self.system_prompt += f'\nCurrent date: {datetime.now().strftime("%A, %B %d, %Y")}'
        self.system_prompt += f'\nWorking directory: {os.getcwd()}'

        parts: list[str] = [self.system_prompt]

        listing = format_skill_listing(self.skills)

        if listing:
            parts.append(listing)

        if self.memory:
            parts.append(self.memory)

        content = '\n\n'.join(parts)

        self.messages.append(system_msg(content, cache=True))


    def run(
        self,
        prompt: str,
        sink: Sink | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:

        active_sink: Sink | None = sink

        if active_sink is not None:
            active_sink.on_turn_start(prompt)

        self.messages.append(user_msg(prompt))

        result = ''

        try:
            result = execution_loop(
                self,
                model=self.model,
                sink=sink,
                cancel_event=cancel_event,
            )

            return result

        finally:
            if active_sink is not None:
                active_sink.on_turn_end(result)


