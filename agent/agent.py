from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agent.client import build_client
from agent.decorator import bind_tool
from agent.loop import execution_loop
from agent.messages import system_msg, user_msg
from agent.sinks import Sink, StdoutSink, wrap_with_ambient
from agent.skills import Skill, format_skill_listing, load_skills
from agent.tool_handler import ToolHandler
from agent.base_tools.extract import extract
from agent.base_tools.load_tool import load_tool
from agent.base_tools.plan import plan
from agent.base_tools.search import search
from agent.base_tools.skill import load_skill
from agent.base_tools.read import read


class Agent:
    def __init__(
        self,
        provider: str = 'vllm',
        model: str | None = None,
        tools: list[dict[str, Any] | Callable] = [],
        system: str | None = None,
        task: str | None = None,
        domain_root: Path | None = None,
        max_iters: int = 100,
    ) -> None:

        # Construction is inert: the client is built lazily on first run() so module-level `agent = Agent(...)` 
        # stays import-safe (no `.env` needed to construct).
        self.provider = provider
        self.client = None
        self.model = model
        
        self.max_iters = max_iters
        self.task = task

        # Initialize Message List
        self.messages: list[dict] = []

        # Tool registry
        self.tools: list[dict[str, Any]] = []
        self.tool_functions: dict[str, Callable] = {}
        self.deferred_tools: dict[str, dict[str, Any]] = {}
        self.loaded_deferred: set[str] = set()

        # Initialize Tool Handler
        self.tool_handler = ToolHandler(self)

        # Initialize Plan
        self.plan: list[dict] = []

        self.system_prompt = (Path(__file__).parent / 'context' / 'system_prompt.md').read_text(encoding='utf-8').strip()

        # If a domain system prompt is passed, append it under a domain header
        if system:
            self.system_prompt += '\n\n<domain>\n' + system.strip() + '\n</domain>'

        # Load skills from the base package plus the domain's skills dir (auto-created); base skills win on name collision.
        skill_roots = [Path(__file__).parent / 'skills']

        if domain_root:
            domain_skills = domain_root / 'skills'
            domain_skills.mkdir(parents=True, exist_ok=True)
            skill_roots.append(domain_skills)

        self.skills: list[Skill] = load_skills(skill_roots)

        # ---- Register base tools ---- #
        self.add_tool(search)
        self.add_tool(extract)
        self.add_tool(read)
        self.add_tool(bind_tool(load_skill, _skills_map={s.name: s for s in self.skills}))
        self.add_tool(bind_tool(load_tool, _deferred_tools=self.deferred_tools, _loaded_deferred=self.loaded_deferred))
        self.add_tool(bind_tool(plan, _plan=self.plan))

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
            print(
                f"[agent] warning: tool '{name}' already registered; "
                f"keeping the existing one and ignoring the duplicate",
                file=sys.stderr,
            )
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
        environment = (
            '<environment>\n'
            f'- Date: {datetime.now().strftime("%A, %B %d, %Y")}\n'
            f'- Working directory: {os.getcwd()}\n'
            '</environment>'
        )

        parts: list[str] = [self.system_prompt, environment]

        listing = format_skill_listing(self.skills)

        if listing:
            parts.append(listing)

        content = '\n\n'.join(parts)

        self.messages.append(system_msg(content, cache=True))

    def run(
        self,
        task: str | None = None,
        sink: Sink | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:

        # Build the provider client once on first run (deferred from __init__); 
        # build_client also resolves any provider default model (e.g. VLLM_MODEL) into self.model.
        if self.client is None:
            self.client, self.model = build_client(self.provider, self.model)

        assert self.model is not None  # resolved by build_client above

        task = task if task is not None else self.task

        if task is None:
            raise ValueError(
                'Agent.run() needs a task — pass one to run() or set Agent(task=...) at init.'
            )

        # Default to StdoutSink so a bare run() still streams output, then wrap with always-on 
        # sinks (Langfuse, metrics, …) so observability follows every run.
        sink = wrap_with_ambient(self, sink if sink is not None else StdoutSink())

        sink.on_turn_start(task)

        self.messages.append(user_msg(task))

        result = ''

        try:
            result = execution_loop(
                self,
                model=self.model,
                max_iters=self.max_iters,
                sink=sink,
                cancel_event=cancel_event,
            )

            return result

        finally:
            sink.on_turn_end(result)
