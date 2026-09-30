"""End-to-end check of the subprocess sandbox and the ExecuteCode tool.

Run: uv run python tests/test_code_execution.py
"""
from __future__ import annotations

import os
from pathlib import Path

from agent_harness.base_tools.helpers.sandbox import SubprocessSandbox
from agent_harness.decorator import bind_tool
from agent_harness.base_tools.execute_code import execute_code


def main() -> None:
    os.environ['SANDBOX_SECRET'] = 'must-not-leak'

    with SubprocessSandbox(env={'ALLOWED_KEY': 'visible'}) as sb:
        tool = bind_tool(execute_code, _sandbox=sb)['function']

        # State persists across calls
        assert tool(code='x = 21').payload == '[no output]'
        assert tool(code='x * 2').payload == '42'

        # Env is scrubbed, except what the caller passes explicitly
        assert tool(code="import os; os.environ.get('SANDBOX_SECRET')").payload == '[no output]'
        assert tool(code="os.environ['ALLOWED_KEY']").payload == "'visible'"

        # Errors are captured with a traceback and error status
        err = tool(code='1 / 0')
        assert err.status == 'error' and 'ZeroDivisionError' in err.payload, err

        # Exceptions don't kill the kernel — state is still there
        assert tool(code='print(x)').payload == '21'

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

    print('all sandbox checks passed')


if __name__ == '__main__':
    main()
