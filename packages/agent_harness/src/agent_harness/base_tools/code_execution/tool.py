"""Run Python in the agent's sandbox. State persists across calls."""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
from typing import Annotated, Any

from agent_harness.base_tools.code_execution.config import SandboxConfig
from agent_harness.base_tools.code_execution.sandbox import MAX_AGENT_DEPTH, SANDBOX_DEPTH, SubprocessSandbox
from agent_harness.base_tools.code_execution.screening import screen_code
from agent_harness.client import HOSTED_PROVIDER_ENV
from agent_harness.tooling.decorator import Param, agent_tool, bind_tool
from agent_harness.tooling.result import ToolResult

# The provider whose credentials `recursive_agents` passes into the kernel
RECURSIVE_PROVIDER = 'openrouter'

#     ================================
# --> Helper funcs
#     ================================


def _credentials_note(env: dict[str, str]) -> str:
    """Tell the model which credentials exist in the kernel, by name only."""
    if not env:
        return 'No credentials are available in os.environ. Do not search files or the system for any.'

    names = ', '.join(sorted(env))

    return (
        f'Credentials available in os.environ: {names}. These are the only ones you have. '
        'Read them with os.environ, never print their values, and do not search files or the system for others.'
    )


def _recursive_agents_env() -> dict[str, str]:
    """The host's OpenRouter credentials for a `recursive_agents` sandbox; raises when they cannot work.

    Checked at construction, so a misconfigured sandbox fails before the model
    ever writes code that builds an agent.
    """
    key_var, url_var, _ = HOSTED_PROVIDER_ENV[RECURSIVE_PROVIDER]

    if SANDBOX_DEPTH + 1 > MAX_AGENT_DEPTH:
        raise RuntimeError(
            f'execute_code_tool: SandboxConfig(recursive_agents=True) at sandbox depth {SANDBOX_DEPTH}: '
            f'agents built in this kernel could not build agents of their own (MAX_AGENT_DEPTH={MAX_AGENT_DEPTH})'
        )

    if not os.environ.get(key_var):
        raise RuntimeError(f'execute_code_tool: SandboxConfig(recursive_agents=True) needs {key_var}, which is not set')

    return {var: os.environ[var] for var in (key_var, url_var) if os.environ.get(var)}


def _subagents_note() -> str:
    """Teach the model to build sub-agents in code. Only added with `recursive_agents=True`."""
    return f'''
Sub-agents: the `agent_harness` library is installed here, so code can also build and run another agent,
for work worth delegating (e.g. one sub-agent per ticker or per document). Define its tools, build it, print its answer:

```python
from agent_harness import Agent
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult
from agent_harness.base_tools.code_execution.tool import execute_code_tool

@agent_tool
def my_tool(x: int) -> ToolResult:
    """One-line description the sub-agent sees."""
    return ToolResult(str(x * 2), status='ok')  # status is required: 'ok' or 'error'
```

`@agent_tool` takes optional keyword flags: `@agent_tool(name='FetchQuote', safe_parallel=True)`.
- `name`: the tool name the sub-agent sees (default: the function name).
- `safe_parallel`: True lets several calls in one turn run concurrently. Only for read-only tools that share no state.
- `deferred`: True hides the full schema until the sub-agent loads it with LoadTool. Only worth it for agents with many tools.
Parameter details go in `Annotated[T, Param(description=..., min_val=..., max_val=..., enum=[...])]`
(`from typing import Annotated`, `from agent_harness.tooling.decorator import Param`).

```python
sub = Agent(system='<role>...</role>', provider='{RECURSIVE_PROVIDER}', model='<model id>', tools=[my_tool, execute_code_tool()])
print(sub.run('task for the sub-agent'))
```

You do not have to do this in one call. Kernel state persists, so you can build a sub-agent in steps:
write a tool in one call, test it by calling it directly, fix it, write the system prompt as a variable,
then build and run the agent in a later call. Its tools run in this kernel, so they can use any variable
or file you already have (e.g. a DataFrame you loaded); pass data into the task string or system prompt as needed.
A timeout or crash restarts the kernel and drops these definitions, so save expensive data to files.

Only what you print comes back. A sub-agent can take many minutes, so pass a generous `timeout`.
Sub-agents may execute code but cannot build agents of their own. For more detail, read the source
(e.g. `inspect.getsource(Agent)`).'''


def _packages_note(packages: list[str]) -> str:
    """Tell the model every library it can import: the host's plus the extra `packages`.

    Reads the host list in-process, which matches the kernel because the
    sandbox runs on this interpreter (`sys.executable`) by default.
    """
    host = {dist.metadata['Name'] for dist in importlib.metadata.distributions()}
    names = ', '.join(sorted(host | set(packages), key=str.lower))

    return (
        f'Installed packages (pip distribution names), ready to import alongside the standard library: {names}. '
        'Prefer these over installing new ones.'
    )


#     ================================
# --> Tool
#     ================================


@agent_tool(name='ExecuteCode')
def execute_code(
    code: Annotated[str, Param(description='Python source to run.')],
    timeout: Annotated[int, Param(description='Seconds before the run is killed. Default 60; raise it for long runs such as a sub-agent.', min_val=1, max_val=1800)] = 60,
    reset: Annotated[bool, Param(description='Restart the kernel first, dropping all variables.')] = False,
    _sandbox: SubprocessSandbox = None,  # type: ignore[assignment] injected via bind_tool
    _j_screen: bool = False,  # injected via bind_tool; the model cannot switch it off
) -> ToolResult:
    """
    Execute Python code in a persistent sandbox kernel. Use this for anything
    that involves code: calculations, data analysis, simulations, API calls,
    reading or writing files. Never do arithmetic or data work in your head
    when code can do it. Variables, imports and
    functions defined in one call are available in later calls. A bare
    expression on the last line is echoed like a REPL. Files written land in
    the sandbox workspace. A timeout restarts the kernel and loses all state.
    """
    # Screen first: a blocked call never reaches the kernel, and the denial
    # tells the model which rule fired and what is allowed instead.
    if _j_screen:
        screen = screen_code(code, _sandbox.workspace)

        if screen.blocked:
            return ToolResult(screen.reason, status='error')

    if reset:
        _sandbox.reset()

    result = _sandbox.exec(code, timeout=timeout)

    parts: list[str] = []

    if result.stdout:
        parts.append(result.stdout.rstrip('\n'))

    if result.stderr:
        parts.append('--- stderr ---\n' + result.stderr.rstrip('\n'))

    # Oversized output is already trimmed to head + tail by the kernel.
    payload = '\n'.join(parts) or '[no output]'

    return ToolResult(payload, status='ok' if result.ok else 'error')


def execute_code_tool(
    config: SandboxConfig = SandboxConfig(),
    working_dir: Path | None = None,
) -> dict[str, Any]:
    """Return a registrable ExecuteCode tool backed by its own sandbox.

    The sandbox lives as long as the process and cleans up at exit, so
    `Agent(tools=[execute_code_tool()])` is all a caller needs. `config`
    holds the sandbox options (secrets, extra packages, code screen); see
    `SandboxConfig`. `working_dir` is where the kernel runs: None means a
    fresh temp dir removed at exit, a caller-supplied dir is never removed.
    With `config.j_screen`, fails fast here if `OPENROUTER_API_KEY` is missing.
    With `config.recursive_agents`, the host's OpenRouter credentials join `env`
    and the description teaches building sub-agents; fails fast here if the key
    is missing or this process is already at the agent depth limit.

    The working directory, the names in `env` (never the values) and every
    importable package (host plus `packages`) are appended to the tool description, so the model knows where
    it is, which credentials it has and what it can import, and does not go
    looking for any of them on disk.
    """
    
    if config.j_screen and not os.environ.get('OPENROUTER_API_KEY'):
        raise RuntimeError('execute_code_tool: SandboxConfig(j_screen=True) needs OPENROUTER_API_KEY, which is not set')

    env = dict(config.env)
    packages = list(config.packages)

    # The flag, not a key spotted in `env`, is what enables sub-agents
    if config.recursive_agents:
        env |= _recursive_agents_env()

    sandbox = SubprocessSandbox(working_dir, env=env, packages=packages)

    subagents_note = _subagents_note() if config.recursive_agents else ''

    tool = bind_tool(execute_code, _sandbox=sandbox, _j_screen=config.j_screen)

    tool['description'] = (
        f'{tool["description"]}\n\nWorking directory: {sandbox.workspace}\n'
        'Save every file here with a relative path (e.g. "defs.parquet"). Writes to absolute paths such as /tmp are blocked.\n'
        f'{_credentials_note(env)}\n{_packages_note(packages)}{subagents_note}'
    )

    return tool
