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
        env: The only host secrets model code may read via `os.environ`,
            e.g. `{'FMP_API_KEY': key}`. Their names (never the values) are
            listed in the tool description.
        packages: Extra pip requirements on top of the host environment's
            libraries, e.g. `['pandas']`. Installed into a separate cached venv.
        j_screen: Run every call past the Jev code screen
            (`code_execution/screening.py`) before it executes; a blocked call
            returns an error result and never reaches the kernel. Needs
            `OPENROUTER_API_KEY`; `execute_code_tool` fails fast if it is missing.
        recursive_agents: Let model code build and run sub-agents with `agent_harness`.
            Copies the host's OpenRouter credentials into the kernel and teaches
            the pattern in the tool description. Off by default: model code can
            then read a billing key. `execute_code_tool` fails fast if the key is
            missing or the sandbox is already at the agent depth limit.
    """

    env: Mapping[str, str] = field(default_factory=dict)
    packages: Sequence[str] = ()
    j_screen: bool = False
    recursive_agents: bool = False
