"""AgentWorkspace: one agent's persistent home, configured once and attached to an agent.

A workspace owns a folder on disk and everything the agent keeps there. The
folder lives in the user's home, outside any repository, so agent data never
lands in source control:

    ~/.agent_harness/workspaces/<name>/      (base overridable via AGENT_HARNESS_HOME)
    └── sandbox/            ExecuteCode's working directory (files persist across runs)
        └── skills/
            ├── curated/    human-reviewed skills, re-copied from `curated_skills` on every attach
            └── learned/    the agent's own skills, read and written by its code

`attach(agent)` syncs the curated skills, then plugs the workspace into a built
agent through the agent's public API: the ExecuteCode tool, the skills protocol
in the system prompt, and the `<skills>` index as a dynamic context provider. The engine never imports this
module; the dependency runs one way, workspace -> engine.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from agent_harness.base_tools.code_execution.tool import execute_code_tool
from agent_harness.workspace.skills import CURATED, LEARNED, SKILLS_PROMPT, SLUG, SkillsIndex

if TYPE_CHECKING:
    from agent_harness.agent import Agent

# Not ~/.cache: a cache may be wiped, but workspaces hold skills and memory that must survive
HOME_ENV = 'AGENT_HARNESS_HOME'
DEFAULT_HOME = Path.home() / '.agent_harness'


#     ================================
# --> Helper funcs
#     ================================


def workspaces_dir() -> Path:
    """Base folder holding every workspace. Read at call time so tests can point it at a temp dir."""
    home = os.environ.get(HOME_ENV)

    return (Path(home) if home else DEFAULT_HOME) / 'workspaces'


#     ================================
# --> Workspace
#     ================================


class AgentWorkspace:
    """A persistent sandbox and skill library for one agent.

    Args:
        name: Workspace name, a lowercase slug (e.g. 'analyst'). The same name
            reopens the same folder; created if missing and never deleted.
        curated_skills: Folder of human-reviewed skills (`<name>/SKILL.md`), usually
            version-controlled. Copied fresh into `skills/curated/` on every attach,
            so edits the agent makes there never persist. None means learned skills only.
        env: Secrets the agent's code may read via `os.environ`, e.g. `{'FMP_API_KEY': key}`.
        packages: Extra pip requirements for the sandbox, e.g. `['pandas']`.
        j_screen: Run every ExecuteCode call past the code screen first.
    """

    def __init__(
        self,
        name: str,
        curated_skills: str | Path | None = None,
        env: dict[str, str] | None = None,
        packages: list[str] | None = None,
        j_screen: bool = False,
    ) -> None:

        # A slug, not a path, so a workspace can only ever live under workspaces_dir()
        if not SLUG.fullmatch(name):
            raise ValueError(f'AgentWorkspace: name must be a lowercase slug like "analyst", got {name!r}')

        # Fail at construction, not at attach, when the curated source is missing
        if curated_skills is not None and not Path(curated_skills).is_dir():
            raise ValueError(f'AgentWorkspace: curated_skills folder not found: {curated_skills}')

        self.name = name
        self.curated_source = Path(curated_skills).resolve() if curated_skills is not None else None
        self.root = workspaces_dir() / name
        self.sandbox_dir = self.root / 'sandbox'
        self.skills_dir = self.sandbox_dir / 'skills'

        # The kernel starts in sandbox_dir, so it must exist before the first call
        (self.skills_dir / LEARNED).mkdir(parents=True, exist_ok=True)

        # A caller-supplied workspace is never deleted, so files outlive the process
        self.code_tool = execute_code_tool(
            workspace=self.sandbox_dir, 
            env=env, packages=packages, 
            j_screen=j_screen
        )
        self.skills = SkillsIndex(self.skills_dir)

    def attach(self, agent: Agent) -> None:
        """Plug this workspace into `agent`. Call once, before the agent's first run.

        Raises if the agent already has an ExecuteCode tool (it would silently
        keep that sandbox instead of this one) or has already run (the system
        prompt can no longer be extended).
        """
        if 'ExecuteCode' in agent.tool_functions:
            raise ValueError('attach: agent already has an ExecuteCode tool; the workspace provides its own')

        # First, so an agent that already ran fails before anything is registered
        agent.extend_system_prompt(SKILLS_PROMPT)

        self.sync_curated()

        agent.add_tool(self.code_tool)
        agent.add_dynamic_provider(self.skills)

    def sync_curated(self) -> None:
        """Replace `skills/curated/` with a fresh copy of the curated source.

        Discards any edits the agent made to curated skills, which is what keeps
        them read-only without file permissions. With no source, the tier is emptied.
        """
        curated_dir = self.skills_dir / CURATED

        if curated_dir.exists():
            shutil.rmtree(curated_dir)

        if self.curated_source is None:
            return

        shutil.copytree(self.curated_source, curated_dir)
