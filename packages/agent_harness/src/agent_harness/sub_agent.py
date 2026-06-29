"""SubAgent: an Agent deployed as a tool of a parent agent.

A parent registers a roster of `SubAgentConfig` configs at construction
(`Agent(subagents=[...])`); the `DeploySubagent` tool (see
`base_tools/deploy_subagent.py`) lets the parent model hand a task to one of
them by name. Each deployment instantiates a fresh `SubAgent`, so message
history is isolated per call.

SubAgents are deliberately constrained: no gates, no hooks, no subagents of
their own, and no `domain_root` (so no domain skills or memory). Interception
and observation belong to the parent that owns the run, not to a tool-invoked
sub-run.
"""
from __future__ import annotations

from typing import Any

from agent_harness.agent import Agent
from agent_harness.base_tools.deploy_subagent import SubAgentConfig


class SubAgent(Agent):
    """Agent variant deployed as a tool. Cannot register gates or hooks."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Guard against recursive nesting: a subagent may not own subagents
        # of its own. `from_spec` never forwards them, so this only trips on
        # a direct SubAgent(subagents=[...]) construction.
        if kwargs.get('subagents'):
            raise NotImplementedError('SubAgents cannot have subagents of their own.')

        # Subagents have no domain skills or memory, so domain_root is moot.
        if kwargs.get('domain_root'):
            raise NotImplementedError('SubAgents cannot have a domain_root (no skills or memory).')

        super().__init__(*args, **kwargs)

    def add_gate(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError('SubAgents cannot register gates.')

    def add_hook(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError('SubAgents cannot register hooks.')

    @classmethod
    def from_spec(cls, spec: SubAgentConfig) -> 'SubAgent':
        """Instantiate a fresh SubAgent from a spec (isolated history).

        `subagents` and `domain_root` are intentionally not forwarded: a
        deployed subagent has no DeploySubagent tool of its own (no recursive
        nesting) and no domain skills or memory.
        """
        return cls(
            provider=spec.provider,
            model=spec.model,
            tools=list(spec.tools),
            system=spec.system,
            max_iters=spec.max_iters,
        )
