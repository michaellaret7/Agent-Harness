from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, cast

from agent_harness.client import build_client
from agent_harness.decorator import bind_tool
from agent_harness.gates import Gate
from agent_harness.hooks import Hook, HookEvent
from agent_harness.loop import execution_loop
from agent_harness.messages import system_msg, user_msg
from agent_harness.sinks import MultiSink, Sink, StdoutSink, compose_sinks
from agent_harness.sinks.hooks import HookSink
from agent_harness.skills import Skill, format_skill_listing, load_skills
from agent_harness.tool_handler import ToolHandler
from agent_harness.base_tools.extract import extract
from agent_harness.base_tools.load_tool import load_tool
from agent_harness.base_tools.plan import plan
from agent_harness.base_tools.search import search
from agent_harness.base_tools.skill import load_skill
from agent_harness.base_tools.read import read
from agent_harness.base_tools.deploy_subagent import SubAgentConfig, make_deploy_subagent_tool


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
        subagents: list[SubAgentConfig] = [],
    ) -> None:

        # Construction is inert: the client is built lazily on first run() so module-level `agent = Agent(...)` 
        # stays import-safe (no `.env` needed to construct).
        self.provider = provider
        self.model = model
        self.client = None
        
        self.max_iters = max_iters
        self.task = task

        # Initialize Message List
        self.messages: list[dict] = []

        # Tool registry
        self.tools: list[dict[str, Any]] = []
        self.tool_functions: dict[str, Callable] = {}
        self.deferred_tools: dict[str, dict[str, Any]] = {}
        self.loaded_deferred: set[str] = set() # This needs to be a set data structure to avoid duplicate tool names

        # Hook registry: event -> [(tool_filter, callback)]. Driven by HookSink.
        self.hooks: dict[str, list[tuple[frozenset[str] | None, Hook]]] = {}

        # Gate registry: [(tool_filter, gate)]. A flat list, not keyed by event
        # like hooks — gates fire at one point (before tool dispatch). Consulted
        # by ToolHandler._dispatch, which consumes each verdict.
        self.gates: list[tuple[frozenset[str] | None, Gate]] = []

        # Subagent registry: name -> spec. Populated below only when specs are
        # passed; the DeploySubagent tool reads the registry from this dict.
        self.subagents: dict[str, SubAgentConfig] = {}

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

        # Register the DeploySubagent tool only when a roster was passed —
        # agents without subagents never see the tool.
        if subagents:
            self.subagents = {spec.name: spec for spec in subagents}

            self.add_tool(make_deploy_subagent_tool(self.subagents)) # Only add the subagent deploy tool if subagents are passed

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

    def add_hook(
        self,
        event: HookEvent,
        fn: Hook,
        *,
        tool: str | Iterable[str] | None = None,
    ) -> None:
        """Register a hook fired on a lifecycle event.

        The hook receives a single `HookContext` and its return value is
        ignored — it never blocks or vetoes the run (side effects only). It
        runs synchronously on the worker thread, so a hook that must not
        stall the loop should spawn its own thread and return immediately.

        `tool` filters `tool_start` / `tool_end` to one or more tool names —
        pass a single name or an iterable of names. It is meaningless on
        non-tool events (nothing will match).
        """

        names = frozenset([tool]) if isinstance(tool, str) else frozenset(tool) if tool is not None else None

        # Fail fast on a typo'd filter: every tool is registered in __init__
        # before any add_hook call, so an unknown name here is a caller bug,
        # not a not-yet-registered tool.
        if names is not None:
            unknown = names - self.tool_functions.keys()

            if unknown:
                raise ValueError(
                    f'add_hook: no registered tool(s) named {sorted(unknown)}. '
                    f'Available: {sorted(self.tool_functions)}'
                )

        self.hooks.setdefault(event, []).append((names, fn))

    def add_gate(
        self,
        fn: Gate,
        *,
        tool: str | Iterable[str] | None = None,
    ) -> None:
        """Register a gate consulted before a tool call is dispatched.

        The gate receives a single `GateContext` and returns a `GateVerdict`
        whose verdict is consumed: `allow` runs the call unchanged, `deny`
        blocks it (the reason becomes the tool result), `rewrite` runs it
        with substituted arguments. This is the interceptor counterpart to
        `add_hook` — a hook observes, a gate decides.

        Gates run synchronously in `ToolHandler._dispatch`, in registration
        order, before the tool function. A `rewrite` is visible to every
        later gate; a `deny` short-circuits the rest.

        `tool` filters the gate to one or more tool names — pass a single
        name or an iterable. `None` (the default) applies the gate to every
        tool call.
        """

        names = frozenset([tool]) if isinstance(tool, str) else frozenset(tool) if tool is not None else None

        # Fail fast if the tool gate tool is not registered in the agents tool registry
        if names is not None:
            unknown = names - self.tool_functions.keys()

            if unknown:
                raise ValueError(
                    f'add_gate: no registered tool(s) named {sorted(unknown)}. '
                    f'Available: {sorted(self.tool_functions)}'
                )

        # Add the name of the gate and the function object to the agents gates list
        self.gates.append((names, fn))

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
        sink = compose_sinks(self, sink if sink is not None else StdoutSink())

        # Compose the HookSink only when hooks are registered, so unused agents pay nothing.
        if self.hooks:
            sink = cast(Sink, MultiSink([sink, HookSink(self)]))

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
