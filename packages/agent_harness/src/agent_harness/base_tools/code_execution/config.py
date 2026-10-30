"""SandboxConfig: the one definition of what an ExecuteCode sandbox may see and do.

Both entry points take it, so each option is declared once:

    execute_code_tool(SandboxConfig(...))                  ephemeral: temp working dir, removed at exit
    AgentWorkspace(name, sandbox=SandboxConfig(...))       persistent: ~/.agent_harness/workspaces/<name>/sandbox

Where the sandbox runs (its working directory) is not part of the config: the
caller of `execute_code_tool` or the workspace decides that.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SandboxConfig:
    """Options for one ExecuteCode sandbox.

    Args:
        env: Host secrets exposed to model code; only names appear in the tool schema.
        packages: Extra requirements installed in a cached venv alongside host libraries.
        j_screen: Screen each call before execution. Requires OPENROUTER_API_KEY;
            missing credentials fail tool construction, blocked calls return errors.
        recursive_agents: Allow nested agents and expose host OpenRouter credentials
            to model code. Off by default; construction fails without credentials or
            at the agent depth limit.
        subagent_model: Suggested model for recursive agents; enforced by prompt only.
            None lets model code choose.
    """

    env: Mapping[str, str] = field(default_factory=dict)
    packages: Sequence[str] = ()
    j_screen: bool = False
    recursive_agents: bool = False
    subagent_model: str | None = None
