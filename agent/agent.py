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
from agent.base_tools.extract import extract
from agent.base_tools.load_tool import tool_loader
from agent.base_tools.plan import bind_plan
from agent.base_tools.search import search
from agent.base_tools.skill import skill_loader

load_dotenv()

class Agent:
    def __init__(
        self,
        provider: str = 'vllm',
        model: str | None = None,
        tools: list[dict[str, Any] | Callable] = [],
        prompt: str | None = None,
        domain_root: Path | None = None,
    ) -> None:

        self.client, self.model = build_client(provider, model)
        self.provider = provider

        # Initialize Message List
        self.messages: list[dict] = []

        # Tool registry
        self.tools: list[dict[str, Any]] = []
        self.tool_functions: dict[str, Callable] = {}
        self.deferred_tools: dict[str, dict[str, Any]] = {}
        self.loaded_deferred: set[str] = set()

        # Initialize Tool Handler
        self.tool_handler = ToolHandler(self)

        # Initialize Base Skills
        self.skills: list[Skill] = load_skills(Path(__file__).parent / 'skills')

        # Initialize Plan
        self.plan: list[dict] = []

        self.system_prompt = (Path(__file__).parent / 'context' / 'system_prompt.md').read_text(encoding='utf-8').strip()

        # If prompt is passed to the agent, append it to the system prompt
        if prompt:
            self.system_prompt += '\n\n' + prompt.strip()

        # If a domain root is provided, load domain skills (auto-creating the
        # dir so skill_builder can write into it) and read memory.md if present.
        self.memory = ''

        if domain_root:
            skills_dir = domain_root / 'skills'
            skills_dir.mkdir(parents=True, exist_ok=True)

            existing = {s.name for s in self.skills}
            self.skills.extend(s for s in load_skills(skills_dir) if s.name not in existing)

            memory_file = domain_root / 'memory.md'

            if memory_file.is_file():
                self.memory = memory_file.read_text(encoding='utf-8').strip()

        # ---- Register base tools ---- #
        self.add_tool(search)
        self.add_tool(extract)
        self.add_tool(skill_loader(self.skills))
        self.add_tool(tool_loader(self.deferred_tools, self.loaded_deferred))
        self.add_tool(bind_plan(self.plan))

        if tools:
            for tool in tools:
                self.add_tool(tool)

        self.build_initial_context()

    def add_tool(self, tool: dict[str, Any] | Callable) -> None:
        """Register a tool.

        Accepts either a tool dict (`{name, description, parameters, function, deferred?}`)
        or a function decorated with `@agent_tool` (carries `.tool` attr).
        No-op if a tool with the same name is already registered.

        If `deferred` is True, the entry stored in `self.tools` carries a
        truncated description (first sentence + ` [deferred]` marker) and an
        empty parameter schema. The full canonical dict is stashed in
        `self.deferred_tools` so `load_tool` can return it on demand.
        """
        if callable(tool) and hasattr(tool, 'tool'):
            tool = tool.tool  # type: ignore[attr-defined]

        if not isinstance(tool, dict):
            raise TypeError(
                f'add_tool: expected dict or @agent_tool function, got {type(tool).__name__}'
            )

        name = tool['name']

        if name in self.tool_functions:
            return

        description = tool['description']
        parameters = tool['parameters']

        if tool.get('deferred', False):
            description = f'{description.split(".", 1)[0]}. [deferred]'
            parameters = {'type': 'object', 'properties': {}}
            self.deferred_tools[name] = tool

        self.tools.append({
            'type': 'function',
            'function': {
                'name': name,
                'description': description,
                'parameters': parameters,
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
