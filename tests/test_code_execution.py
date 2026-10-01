"""End-to-end check of the subprocess sandbox and the ExecuteCode tool.

Run: uv run python tests/test_code_execution.py
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_harness.base_tools.code_execution.tool import execute_code, execute_code_tool
from agent_harness.base_tools.code_execution import sandbox as sandbox_module
from agent_harness.base_tools.code_execution.sandbox import SubprocessSandbox
from agent_harness.tooling.decorator import bind_tool

REAL_VENV_CACHE = sandbox_module.VENV_CACHE


def check_kernel() -> None:
    """Persistent state, scrubbed env, error capture, timeouts and cleanup."""
    with SubprocessSandbox(env={'ALLOWED_KEY': 'visible'}) as sb:
        tool = bind_tool(execute_code, _sandbox=sb)['function']

        # State persists across calls
        assert tool(code='x = 21').payload == '[no output]'
        assert tool(code='x * 2').payload == '42'

        # Env is scrubbed, except what the caller passes explicitly
        assert tool(code="import os; os.environ.get('SANDBOX_SECRET')").payload == '[no output]'
        assert tool(code="os.environ['ALLOWED_KEY']").payload == "'visible'"

        # Errors are captured with a traceback showing only the model's frames, and error status
        err = tool(code='def f():\n    return 1 / 0\nf()')
        assert err.status == 'error' and 'ZeroDivisionError' in err.payload, err
        assert 'in f' in err.payload and 'kernel.py' not in err.payload, err

        # A syntax error is reported without any kernel frames either
        assert 'kernel.py' not in tool(code='def (').payload

        # Closing stdout only affects that call: the next call prints normally and state survives
        tool(code='import sys; sys.stdout.close()')
        assert tool(code='print(x)').payload == '21'

        # Exceptions don't kill the kernel — state is still there
        assert tool(code='print(x)').payload == '21'

        # Child process output is captured instead of corrupting the protocol
        child = tool(code="import subprocess, sys; subprocess.run([sys.executable, '-c', 'print(123456)']); print('after')")
        assert child.payload == '123456\nafter', child
        assert tool(code='x').payload == '21'

        # stdin is empty, so reading it cannot swallow the next request
        assert 'EOFError' in tool(code='input()').payload
        assert tool(code='x').payload == '21'

        # Runaway output is trimmed to head + tail inside the kernel
        flood = tool(code="print('x' * 100_000); print('END')")
        assert len(flood.payload) < 25_000 and 'bytes truncated' in flood.payload and flood.payload.endswith('END'), len(flood.payload)

        # Files land in the workspace
        tool(code="open('out.txt', 'w').write('hi')")
        assert (sb.workspace / 'out.txt').read_text() == 'hi'

        # Timeout kills and restarts the kernel, dropping state
        slow = tool(code='import time; time.sleep(5)', timeout=1)
        assert slow.status == 'error' and 'timed out' in slow.payload, slow
        assert tool(code='x').status == 'error'  # NameError after restart

        # reset=True drops state explicitly
        tool(code='y = 1')
        assert tool(code='y', reset=True).status == 'error'

        workspace: Path = sb.workspace

    assert not workspace.exists(), 'temp workspace should be removed on stop'


def check_packages() -> None:
    """`packages=` layers extras over the host venv's libraries without touching the host venv."""
    with SubprocessSandbox(packages=['tabulate', 'idna==3.6']) as sb:
        tool = bind_tool(execute_code, _sandbox=sb)['function']

        # The extra package is importable in the sandbox, and the host venv did not gain it
        assert tool(code='import tabulate; tabulate.__name__').payload == "'tabulate'"
        assert importlib.util.find_spec('tabulate') is None

        # The host venv's libraries are inherited, editable installs included
        assert tool(code='import pydantic, agent_harness; pydantic.__name__').payload == "'pydantic'"

        # On a version clash the sandbox's copy wins, and the host keeps its own
        assert tool(code='import idna; idna.__version__').payload == "'3.6'"
        assert importlib.metadata.version('idna') != '3.6'

        # The kernel runs on the cached venv's interpreter, not the host's
        assert 'sandbox-venvs' in tool(code='import sys; sys.prefix').payload


def check_concurrent_build() -> None:
    """Two sandboxes building the same package set at once both end up on one intact venv."""
    cache = Path(tempfile.mkdtemp(prefix='venv_cache_'))
    sandbox_module.VENV_CACHE = cache

    try:
        sandboxes = [SubprocessSandbox(packages=['six']) for _ in range(2)]

        with ThreadPoolExecutor(2) as pool:
            prefixes = list(pool.map(lambda sb: sb.exec('import six, sys; print(sys.prefix)').stdout.strip(), sandboxes))

        for sb in sandboxes:
            sb.stop()

        assert prefixes[0] == prefixes[1], prefixes
        assert [p.name for p in cache.iterdir()] == [Path(prefixes[0]).name], list(cache.iterdir())

    finally:
        sandbox_module.VENV_CACHE = REAL_VENV_CACHE
        shutil.rmtree(cache, ignore_errors=True)


def check_credentials_note() -> None:
    """The tool description names the credentials in `env`, and never shows their values."""
    described = execute_code_tool(env={'FMP_API_KEY': 'secret-value', 'DB_KEY': 'other-secret'})['description']

    assert 'DB_KEY, FMP_API_KEY' in described and 'Working directory: ' in described, described
    assert 'secret-value' not in described and 'other-secret' not in described

    # With no env the model is told there is nothing to look for
    assert 'No credentials are available' in execute_code_tool()['description']

    # The shared tool dict is not mutated, so each tool carries only its own note
    assert 'os.environ' not in execute_code.tool['description']


def main() -> None:
    os.environ['SANDBOX_SECRET'] = 'must-not-leak'

    check_kernel()
    print('kernel checks passed')

    check_packages()
    print('package checks passed')

    check_concurrent_build()
    print('concurrent build passed')

    check_credentials_note()
    print('credentials note passed')

    print('all sandbox checks passed')


if __name__ == '__main__':
    main()
