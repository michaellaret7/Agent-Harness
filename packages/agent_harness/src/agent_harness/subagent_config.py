"""Shared configuration for deployable subagents."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


#     ================================
# --> Helper funcs
#     ================================


def _no_tools() -> list[dict[str, Any] | Callable]:
    """Return an empty tool roster for a subagent."""

    return []


#     ================================
# --> Spec
#     ================================


@dataclass(frozen=True)
class SubAgentConfig:
    """Config for one deployable subagent.

    `name` is the key the parent model deploys by; `description` tells the
    parent when to use it (surfaced in the DeploySubagent tool schema). The
    rest mirror `Agent.__init__` and are forwarded by `SubAgent.from_spec`.

    `make_tools` is called once per deployment, so tools that own state (a
    sandbox behind `execute_code_tool`, for instance) are fresh for each
    subagent and parallel deployments never share one.
    """

    name: str
    description: str
    system: str | None = None
    make_tools: Callable[[], Sequence[dict[str, Any] | Callable]] = _no_tools
    provider: str = 'openrouter'
    model: str | None = None
    max_iters: int = 100
    reasoning_effort: str | None = None
