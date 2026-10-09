"""Run Python in the agent's sandbox. State persists across calls."""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
from typing import Annotated, Any

from agent_harness.base_tools.code_execution.sandbox import MAX_AGENT_DEPTH, SANDBOX_DEPTH, SubprocessSandbox
from agent_harness.base_tools.code_execution.screening import screen_code
from agent_harness.client import HOSTED_PROVIDER_ENV
from agent_harness.tooling.decorator import Param, agent_tool, bind_tool
from agent_harness.tooling.result import ToolResult

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


def _subagents_note(env: dict[str, str]) -> str:
    """Teach the model to build sub-agents in code, when this sandbox may and can.

    Empty unless the kernel is within the agent depth limit and `env` holds a
    hosted provider's API key, so a sub-agent's own ExecuteCode never offers it.
    """
    providers = [name for name, (key_var, _) in HOSTED_PROVIDER_ENV.items() if key_var in env]

    if SANDBOX_DEPTH + 1 > MAX_AGENT_DEPTH or not providers:
        return ''

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

sub = Agent(system='<role>...</role>', provider='{providers[0]}', model='<model id>', tools=[my_tool, execute_code_tool()])
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
    workspace: Path | None = None,
    env: dict[str, str] | None = None,
    packages: list[str] | None = None,
    j_screen: bool = False,
) -> dict[str, Any]:
    """Return a registrable ExecuteCode tool backed by its own sandbox.

    The sandbox lives as long as the process and cleans up at exit, so
    `Agent(tools=[execute_code_tool()])` is all a caller needs. `env` lists
    the only host secrets model code may see, e.g. `{'FMP_API_KEY': key}`.
    `packages` adds pip requirements on top of the host environment's
    libraries, e.g. `['pandas']`. `j_screen` runs every call past the Jev
    code screen (see `code_execution/screening.py`) before it executes; a blocked
    call returns an error result and never reaches the kernel. It needs
    `OPENROUTER_API_KEY` and fails fast here if that is missing.

    The workspace path, the names in `env` (never the values) and every
    importable package (host plus `packages`) are appended to the tool description, so the model knows where
    it is, which credentials it has and what it can import, and does not go
    looking for any of them on disk.
    """
    if j_screen and not os.environ.get('OPENROUTER_API_KEY'):
        raise RuntimeError('execute_code_tool(j_screen=True): OPENROUTER_API_KEY is not set')

    sandbox = SubprocessSandbox(workspace, env=env, packages=packages)

    tool = bind_tool(execute_code, _sandbox=sandbox, _j_screen=j_screen)
    tool['description'] = (
        f'{tool["description"]}\n\nWorking directory: {sandbox.workspace}\n'
        'Save every file here with a relative path (e.g. "defs.parquet"). Writes to absolute paths such as /tmp are blocked.\n'
        f'{_credentials_note(env or {})}\n{_packages_note(packages or [])}{_subagents_note(env or {})}'
    )

    return tool
