from __future__ import annotations

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
from tools.base import bash, edit, glob, grep, read, search, tree, write
from tools.base.skill import make_skill_tool

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
        self.add_tool(**bash.tool)
        self.add_tool(**read.tool)
        self.add_tool(**write.tool)
        self.add_tool(**edit.tool)
        self.add_tool(**glob.tool)
        self.add_tool(**grep.tool)
        self.add_tool(**tree.tool)
        self.add_tool(**search.tool)
        self.add_tool(**make_skill_tool(self.skills))

        self.build_initial_context()

    def add_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        function: Callable,
    ) -> None:
        """Register a tool. No-op if already registered."""
        if name in self.tool_functions:
            return

        self.tools.append({
            'type': 'function',
            'function': {
                'name': name,
                'description': description,
                'parameters': parameters,
            },
        })

        self.tool_functions[name] = function

    def build_initial_context(self) -> None:
        self.system_prompt += f'\nCurrent date: {datetime.now().strftime("%A, %B %d, %Y")}'

        parts: list[str] = [self.system_prompt]

        listing = format_skill_listing(self.skills)

        if listing:
            parts.append(listing)

        if self.memory:
            parts.append(self.memory)

        content = '\n\n'.join(parts)

        self.messages.append(system_msg(content))

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


