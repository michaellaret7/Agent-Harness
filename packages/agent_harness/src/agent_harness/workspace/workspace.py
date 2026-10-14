"""AgentWorkspace: one agent's persistent home, configured once and attached to an agent.

A workspace owns everything the agent keeps on disk. It lives in the user's
home, outside any repository, so agent data never lands in source control:

    ~/.agent_harness/                        (base overridable via AGENT_HARNESS_HOME)
    ├── workspaces/<name>/
    │   └── sandbox/        ExecuteCode's working directory (files persist across runs)
    │       └── skills/
    │           ├── curated/    human-reviewed skills, re-copied from `curated_skills` on every attach
    │           └── learned/    the agent's own skills, read and written by its code
    └── memory/<name>.sqlite    long-term memory (only with memory=True), reached only through the memory tools

The memory store sits in a separate branch, not next to the sandbox, so code
browsing the folders around its working directory never comes across it.

`attach(agent)` syncs the curated skills, then plugs the workspace into a built
agent through the agent's public API: the ExecuteCode and memory tools, the
skills and memory protocols in the system prompt, and the `<skills>` index
appended to the system prompt; with `auto_memory_recall=True`, also the `<memories>`
dynamic context provider. The engine never imports this
module; the dependency runs one way, workspace -> engine.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from agent_harness.base_tools.code_execution.config import SandboxConfig
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from agent_harness.workspace.memory import (
    MEMORY_CONTEXT_PROMPT, MEMORY_PROMPT, ApiEmbedder, Embedder, DynamicMemoryContext, MemoryStore, memory_tools,
)
from agent_harness.workspace.skills import CURATED, LEARNED, SLUG, SkillsIndex, skills_prompt

if TYPE_CHECKING:
    from agent_harness.agent import Agent

# Not ~/.cache: a cache may be wiped, but workspaces hold skills and memory that must survive
HOME_ENV = 'AGENT_HARNESS_HOME'
DEFAULT_HOME = Path.home() / '.agent_harness'


#     ================================
# --> Helper funcs
#     ================================


def harness_home() -> Path:
    """Base folder for all agent data. Read at call time so tests can point it at a temp dir."""
    home = os.environ.get(HOME_ENV)

    return Path(home) if home else DEFAULT_HOME


def workspaces_dir() -> Path:
    """Folder holding every workspace's sandbox."""
    return harness_home() / 'workspaces'


def memory_dir() -> Path:
    """Folder holding every workspace's memory store, apart from the sandboxes."""
    return harness_home() / 'memory'


#     ================================
# --> Workspace
#     ================================


class AgentWorkspace:
    """A persistent sandbox and skill library for one agent, with optional long-term memory.

    Args:
        name: Workspace name, a lowercase slug (e.g. 'analyst'). The same name
            reopens the same folder and memory; created if missing and never deleted.
        curated_skills: Folder of human-reviewed skills (`<name>/SKILL.md`), usually
            version-controlled. Copied fresh into `skills/curated/` on every attach,
            so edits the agent makes there never persist. None means learned skills only.
        sandbox: ExecuteCode options (secrets, extra packages, code screen); see
            `SandboxConfig`. The workspace supplies the working directory.
        memory: Give the agent long-term memory (Remember / Recall / Forget tools and
            the memory protocol). Off by default; when off, no memory file is created.
        embedder: Override the embedder memory uses for semantic search. Defaults to
            `ApiEmbedder()` (voyage-4 via OpenRouter); pass another `Embedder` to change
            models or, in tests, to stay offline. Requires `memory=True`.
        auto_memory_recall: Also show the memories most relevant to the latest request in a
            `<memories>` dynamic context block (`DynamicMemoryContext`). Requires `memory=True`.
    """

    def __init__(
        self,
        name: str,
        curated_skills: str | Path | None = None, 
        sandbox: SandboxConfig = SandboxConfig(),
        memory: bool = False, 
        auto_memory_recall: bool = False,
        embedder: Embedder | None = None, 
    ) -> None:

        # A slug, not a path, so a workspace can only ever live under workspaces_dir()
        if not SLUG.fullmatch(name):
            raise ValueError(f'AgentWorkspace: name must be a lowercase slug like "analyst", got {name!r}')

        if embedder is not None and not memory:
            raise ValueError('AgentWorkspace: embedder needs memory=True')

        if auto_memory_recall and not memory:
            raise ValueError('AgentWorkspace: auto_memory_recall needs memory=True')

        # if curated skills are passed but theres no folder raise an error 
        if curated_skills is not None and not Path(curated_skills).is_dir():
            raise ValueError(f'AgentWorkspace: curated_skills folder not found: {curated_skills}')

        # name of the workspace
        self.name = name
        self.root = workspaces_dir() / name
        self.curated_source = Path(curated_skills).resolve() if curated_skills is not None else None
        self.sandbox_dir = self.root / 'sandbox'
        self.skills_dir = self.sandbox_dir / 'skills'

        # The kernel starts in sandbox_dir, so it must exist before the first call
        (self.skills_dir / LEARNED).mkdir(parents=True, exist_ok=True)

        # A caller-supplied workspace is never deleted, so files outlive the process
        # this gets registered with the agent object
        self.code_tool = execute_code_tool(sandbox, working_dir=self.sandbox_dir)

        # create the skills index object
        self.skills = SkillsIndex(self.skills_dir)

        # Named after the workspace, in its own folder: see the module docstring
        # Semantic search is part of memory, not an extra: the default embedder comes built in
        self.memory = MemoryStore(memory_dir() / f'{name}.sqlite', embedder or ApiEmbedder()) if memory else None
        self.auto_memory_recall = auto_memory_recall

    def attach(self, agent: Agent) -> None:
        """Plug this workspace into `agent`. Call once, before the agent's first run.

        Raises if the agent already has an ExecuteCode tool (it would silently
        keep that sandbox instead of this one) or has already run (the system
        prompt can no longer be extended).
        """

        if 'ExecuteCode' in agent.tool_functions:
            raise ValueError('attach: agent already has an ExecuteCode tool; the workspace provides its own')

        # First, so an agent that already ran fails before anything is registered
        agent.extend_system_prompt(skills_prompt(curated=self.curated_source is not None))

        if self.memory is not None:
            agent.extend_system_prompt(MEMORY_PROMPT)

        if self.auto_memory_recall:
            agent.extend_system_prompt(MEMORY_CONTEXT_PROMPT)

        # this method syncs the curated skills from the curated source to the skills directory
        self.sync_curated()

        # register the code tool with the agent object
        agent.add_tool(self.code_tool)

        # Compacted history goes inside the sandbox if the compactor class exists, so the agent can search it with ExecuteCode
        if agent.compactor is not None:
            agent.compactor.attach_archive(self.sandbox_dir / 'history')

        # register the memory tools with the agent object
        if self.memory is not None:
            for tool in memory_tools(self.memory):
                agent.add_tool(tool)

        # register the dynamic memory context provider with the agent object
        # add it as a dynamic memory context provider so it gets injected every turn
        if self.memory is not None and self.auto_memory_recall:
            agent.add_dynamic_provider(DynamicMemoryContext(self.memory, agent))

        # A snapshot after the sync, kept in the cached system prompt: skills written
        # during this session are listed from the next attach on
        index = self.skills()

        if index is not None:
            agent.extend_system_prompt(index)

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
