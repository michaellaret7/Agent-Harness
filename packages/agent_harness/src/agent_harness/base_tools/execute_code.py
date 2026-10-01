"""Run Python in the agent's sandbox. State persists across calls."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from agent_harness.base_tools.helpers.sandbox import SubprocessSandbox
from agent_harness.decorator import Param, agent_tool, bind_tool
from agent_harness.tool_result import ToolResult

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


#     ================================
# --> Tool
#     ================================


@agent_tool(name='ExecuteCode')
def execute_code(
    code: Annotated[str, Param(description='Python source to run.')],
    timeout: Annotated[int, Param(description='Seconds before the run is killed. Default 60.', min_val=1, max_val=600)] = 60,
    reset: Annotated[bool, Param(description='Restart the kernel first, dropping all variables.')] = False,
    _sandbox: SubprocessSandbox = None,  # type: ignore[assignment] injected via bind_tool
) -> ToolResult:
    """
    Execute Python code in a persistent sandbox kernel. Variables, imports and
    functions defined in one call are available in later calls. A bare
    expression on the last line is echoed like a REPL. Files written land in
    the sandbox workspace. A timeout restarts the kernel and loses all state.
    """
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
) -> dict[str, Any]:
    """Return a registrable ExecuteCode tool backed by its own sandbox.

    The sandbox lives as long as the process and cleans up at exit, so
    `Agent(tools=[execute_code_tool()])` is all a caller needs. `env` lists
    the only host secrets model code may see, e.g. `{'FMP_API_KEY': key}`.
    `packages` adds pip requirements on top of the host environment's
    libraries, e.g. `['pandas']`.

    The workspace path and the names in `env` (never the values) are appended
    to the tool description, so the model knows where it is and which
    credentials it has, and does not go looking for either on disk.
    """
    sandbox = SubprocessSandbox(workspace, env=env, packages=packages)

    tool = bind_tool(execute_code, _sandbox=sandbox)
    tool['description'] = f'{tool["description"]}\n\nWorking directory: {sandbox.workspace}\n{_credentials_note(env or {})}'

    return tool
