from __future__ import annotations

import copy
import inspect
import sys
import threading
from typing import Any, Callable, Iterable, cast

from pydantic import BaseModel

from agent_harness.client import build_client
from agent_harness.context.compaction import CompactionConfig, Compactor
from agent_harness.context.renderer import ContextRenderer, DynamicContextProvider
from agent_harness.tooling.context import CONTEXT_PARAM
from agent_harness.gates import Gate
from agent_harness.hooks import Hook, HookEvent
from agent_harness.loop import execution_loop
from agent_harness.messages import user_msg
from agent_harness.sinks import MultiSink, Sink, StdoutSink, compose_sinks
from agent_harness.sinks.hooks import HookSink
from agent_harness.tooling.handler import ToolHandler
from agent_harness.tooling.result import ToolResult
from agent_harness.base_tools.extract import extract
from agent_harness.base_tools.load_tool import load_tool
from agent_harness.base_tools.plan import plan
from agent_harness.base_tools.search import search
from agent_harness.base_tools.deploy_subagent import make_deploy_subagent_tool
from agent_harness.base_tools.submit_result import SUBMIT_RESULT, Verifier, make_submit_result_tool
from agent_harness.base_tools.code_execution.sandbox import MAX_AGENT_DEPTH, SANDBOX_DEPTH
from agent_harness.subagent_config import SubAgentConfig


class Agent:
    def __init__(
        self,
        provider: str = 'openrouter',
        model: str | None = None,
        tools: list[dict[str, Any] | Callable] = [],
        system: str | None = None,
        max_iters: int = 100,
        subagents: list[SubAgentConfig] = [],
        output_model: type[BaseModel] | None = None,
        verifier: Verifier | None = None,
        reasoning_effort: str | None = None,
        dynamic_context_providers: list[DynamicContextProvider] = [],
        compaction: CompactionConfig | None = None,
        name: str | None = None,
    ) -> None:

        # Agents built in model code may not recurse: refuse inside a nested sandbox
        if SANDBOX_DEPTH > MAX_AGENT_DEPTH:
            raise RuntimeError(
                f'Agent cannot be built at sandbox depth {SANDBOX_DEPTH}: agents created inside '
                'ExecuteCode may run code but may not create further agents'
            )

        # Construction is inert: the client is built lazily on first run() so module-level `agent = Agent(...)` 
        # stays import-safe (no `.env` needed to construct).
        self.provider = provider
        self.model = model
        self.client = None

        # Identity for tracing: Langfuse shows `name` as the trace name
        self.name = name

        # Forwarded to every completions call as `reasoning_effort`; None omits it so
        # non-reasoning models are not rejected. Valid values are model-specific.
        self.reasoning_effort = reasoning_effort
        
        self.max_iters = max_iters

        # make sure the output_model is a pydantic BaseModel subclass
        self.output_model = output_model

        if self.output_model is not None and not (
            isinstance(self.output_model, type) and issubclass(self.output_model, BaseModel)
        ):
            raise TypeError('output_model must be a pydantic BaseModel subclass or None')

        # Validated termination: with output_model set, the run only ends through an
        # accepted SubmitResult call, which stores the validated object here.
        self.verifier = verifier
        self.submission: BaseModel | None = None

        # Initialize Message List
        self.messages: list[dict] = []

        # The user message that opened the current run; compaction pins it word for word
        self.turn_task: dict | None = None

        # Keeps long runs under the context window; None leaves history untouched
        self.compactor = Compactor(compaction) if compaction is not None else None

        # Tool registry
        self.tools: list[dict[str, Any]] = []
        self.tool_functions: dict[str, Callable[..., ToolResult]] = {}
        self.deferred_tools: dict[str, dict[str, Any]] = {}
        self.parallel_tools: set[str] = set()
        self.context_tools: set[str] = set()  # tools that declare `_ctx`; ToolHandler fills it per call

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

        # Raw prompt inputs; ContextRenderer assembles them into the system message
        self.domain_prompt = system
        self.prompt_extensions: list[str] = []

        # ---- Register base tools ---- #
        # Each reads the agent state it needs (model, plan, tool registry) from its `_ctx`.
        self.add_tool(search)
        self.add_tool(extract)
        self.add_tool(plan)

        if tools:
            for tool in tools:
                self.add_tool(tool)

        # Register the DeploySubagent tool only when a roster was passed —
        # agents without subagents never see the tool.
        if subagents:
            self.subagents = {spec.name: spec for spec in subagents}
            self.add_tool(make_deploy_subagent_tool(self.subagents)) # Only add the subagent deploy tool if subagents are passed

        # Owns the model-facing formatting of messages (system message + per-request render)
        self.context_renderer = ContextRenderer(dynamic_providers=dynamic_context_providers)

        self.messages.append(self.context_renderer.build_system_message(self))
    
    def _offer_for_loading(self, name: str) -> None:
        """Add a deferred tool to LoadTool's enum without changing the shared schema."""
        entry = next(t['function'] for t in self.tools if t['function']['name'] == 'LoadTool')
        parameters = copy.deepcopy(entry['parameters'])
        items = parameters['properties']['names']['items']

        items['enum'] = [*items.get('enum', []), name]

        entry['parameters'] = parameters

    def extend_system_prompt(self, block: str) -> None:
        """Append a prompt block and rebuild the system message before the first run."""
        if len(self.messages) > 1:
            raise RuntimeError('extend_system_prompt: conversation already started')

        self.prompt_extensions.append(block)

        # Slice-assign to keep the same list object for any held references
        self.messages[:] = [self.context_renderer.build_system_message(self)]
    
    def add_dynamic_provider(self, provider: DynamicContextProvider) -> None:
        """Register a zero-argument provider called before each model request.

        Its output is included in the trailing `<dynamic_context>` message.
        """
        self.context_renderer.dynamic_providers.append(provider)

    def add_tool(self, tool: dict[str, Any] | Callable) -> None:
        """Register a tool dict or an `@agent_tool` function returning ToolResult.

        Dicts require `name`, `description`, `parameters`, and `function`.
        Duplicate names are ignored with a warning. Deferred tools expose a
        short description; LoadTool provides their full schema on demand.
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

        # if the tool is deferred, we need to add it to the deferred tools registry
        # and also add the LoadTool tool to the tool registry so its available to load the deferred tool
        if tool.get('deferred', False):
            description = f'{description.split(".", 1)[0]}. [deferred]'
            parameters = {'type': 'object', 'properties': {}}
            self.deferred_tools[name] = tool

            # LoadTool only exists to load deferred tools: register it with the first one
            # Check if its registered already, if not add it to the tool registry
            if load_tool.tool['name'] not in self.tool_functions:
                self.add_tool(load_tool)

            # Add the name of the deferred tool to the LoadTool's names enum
            self._offer_for_loading(name)

        self.tools.append({
            'type': 'function',
            'function': {
                'name': name,
                'description': description,
                'parameters': parameters,
            },
        })

        self.tool_functions[name] = tool['function']

        # Record safe_parallel here, from the tool dict: the registered callable
        # may be a functools.partial (bind_tool), which drops the `.tool` attr.
        if tool.get('safe_parallel', False):
            self.parallel_tools.add(name)

        # Read off the signature (inspect follows partials and functools.wraps), so any tool,
        # decorated or hand-written, receives the run context just by declaring `_ctx`
        if CONTEXT_PARAM in inspect.signature(tool['function']).parameters:
            self.context_tools.add(name)

    def add_hook(
        self,
        event: HookEvent,
        fn: Hook,
        *,
        tool: str | Iterable[str] | None = None,
    ) -> None:
        """Register a synchronous lifecycle hook receiving HookContext.

        Return values are ignored. Keep hooks brief to avoid stalling the run.
        `tool` filters tool_start/tool_end by one or more registered names;
        leave it unset for other events.
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
        """Register a gate receiving GateContext and returning a GateVerdict.

        Gates run synchronously before dispatch, in registration order.
        `allow` proceeds, `rewrite` changes arguments for later gates and the
        tool, and `deny` stops dispatch with its reason as the tool result.
        `tool` selects one or more registered names; None applies to all tools.
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

    def run(
        self,
        task: str,
        sink: Sink | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str | BaseModel:

        # Build the provider client once on first run (deferred from __init__); 
        # build_client also resolves any provider default model (e.g. VLLM_MODEL) into self.model.
        if self.client is None:
            self.client, self.model = build_client(self.provider, self.model)

        assert self.model is not None  # resolved by build_client above

        # Default to StdoutSink so a bare run() still streams output, then wrap with always-on
        # sinks (Langfuse, metrics, …) so observability follows every run.
        sink = compose_sinks(self, sink if sink is not None else StdoutSink())

        # Compose the HookSink only when hooks are registered, so unused agents pay nothing.
        if self.hooks:
            sink = cast(Sink, MultiSink([sink, HookSink(self)]))

        sink.on_turn_start(task)

        self.turn_task = user_msg(task)
        self.messages.append(self.turn_task)

        # Registered here rather than in __init__ so an output_model assigned after
        # construction still gets its SubmitResult tool. add_tool is a no-op on repeat runs.
        if self.output_model is not None and SUBMIT_RESULT not in self.tool_functions:
            self.add_tool(make_submit_result_tool(self))

        # Each run must earn its own accepted submission
        self.submission = None

        # Each run starts with no failure streaks or blocked tools
        self.tool_handler.failure_streaks.reset()

        result = ''

        try:
            result = execution_loop(
                self,
                model=self.model,
                max_iters=self.max_iters,
                sink=sink,
                cancel_event=cancel_event,
            )

            if self.output_model is not None:
                if self.submission is None:
                    raise RuntimeError(f'run ended without an accepted {SUBMIT_RESULT} call')

                return self.submission

            return result

        finally:
            # Free-text only — result is never overwritten with the structured model.
            sink.on_turn_end(result)
