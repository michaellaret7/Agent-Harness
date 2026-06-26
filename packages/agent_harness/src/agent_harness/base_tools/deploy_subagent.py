"""DeploySubagent: hand a bounded task to a named subagent.

A parent registers a roster of `SubAgentSpec` configs at construction
(`Agent(subagents=[...])`). `make_deploy_subagent_tool` binds that roster
into the tool the parent model calls; each invocation spins up a fresh
`SubAgent` (isolated history) and returns its final answer.

This module deliberately does NOT import `Agent`/`SubAgent` at module level:
`sub_agent` imports `Agent`, and `agent` imports this module's factory, so a
top-level import here would close a cycle. `SubAgent` is imported inside
`deploy_subagent` at call time — the natural place, since a subagent is only
instantiated when a deployment actually happens.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Annotated, Any, Callable

from agent_harness.decorator import Param, agent_tool, bind_tool
from agent_harness.sinks import LogSink


#     ================================
# --> Spec
#     ================================


@dataclass(frozen=True)
class SubAgentSpec:
    """Config for one deployable subagent.

    `name` is the key the parent model deploys by; `description` tells the
    parent when to use it (surfaced in the DeploySubagent tool schema). The
    rest mirror `Agent.__init__` and are forwarded verbatim by
    `SubAgent.from_spec`.
    """

    name: str
    description: str
    system: str | None = None
    tools: tuple[dict[str, Any] | Callable, ...] = ()
    provider: str = 'vllm'
    model: str | None = None
    max_iters: int = 100


#     ================================
# --> Tool
#     ================================


@agent_tool(name='DeploySubagent')
def deploy_subagent(
    name: Annotated[str, Param(description='Which subagent to deploy.')],
    prompt: Annotated[str, Param(description='The task to hand the subagent.')],
    _registry: dict[str, SubAgentSpec] | None = None,
) -> str:
    """Hand a self-contained task to a named subagent and return its result.

    The subagent runs to completion with its own tools and isolated message
    history, then returns its final answer as this tool's result. Use it to
    delegate a bounded sub-task; pass everything the subagent needs in
    `prompt` — it does not see the parent conversation.
    """
    from agent_harness.sub_agent import SubAgent

    if _registry is None:
        return 'error: DeploySubagent not bound to a registry'

    spec = _registry.get(name)

    if spec is None:
        return f"error: no subagent named '{name}'. Available: {sorted(_registry)}"

    # Subagents always log through a LogSink keyed by name (`agent.<name>`),
    # so a deployment's events land in the logging stream rather than stdout.
    return SubAgent.from_spec(spec).run(prompt, sink=LogSink(name))


def make_deploy_subagent_tool(registry: dict[str, SubAgentSpec]) -> dict[str, Any]:
    """Build the DeploySubagent tool dict bound to `registry`.

    Binds the registry into the hidden `_registry` param, then bakes the
    roster (names + descriptions) into the description and the `name` enum so
    the parent model sees exactly which subagents exist. The parameters schema
    is deep-copied first so each agent's enum stays independent of the shared
    module-level tool dict.
    """
    tool = bind_tool(deploy_subagent, _registry=registry)
    tool['parameters'] = copy.deepcopy(tool['parameters'])

    roster = '\n'.join(f'- {name}: {spec.description}' for name, spec in registry.items())

    tool['description'] = f'{tool["description"]}\n\nAvailable subagents:\n{roster}'
    tool['parameters']['properties']['name']['enum'] = sorted(registry)

    return tool
