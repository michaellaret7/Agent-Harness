"""Each subagent deployment gets its own tools, so parallel deployments never share a sandbox.

Run: uv run python tests/test_subagent_tools.py
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from agent_harness.base_tools.deploy_subagent import SubAgentConfig
from agent_harness.base_tools.execute_code import execute_code_tool
from agent_harness.sub_agent import SubAgent


def main() -> None:
    spec = SubAgentConfig(
        name='analyst',
        description='runs python',
        make_tools=lambda: [execute_code_tool(env={'ROLE': 'analyst'})],
    )

    workers = [SubAgent.from_spec(spec) for _ in range(3)]
    run_code = [agent.tool_functions['ExecuteCode'] for agent in workers]

    # Each deployment sets its own variable, then all read it back at the same time
    for i, tool in enumerate(run_code):
        tool(code=f'worker = {i}')

    with ThreadPoolExecutor(3) as pool:
        seen = list(pool.map(lambda tool: tool(code='import os, time; time.sleep(0.5); worker').payload, run_code))

    assert seen == ['0', '1', '2'], seen

    # The env passed through the factory reached every kernel
    assert run_code[0](code="os.environ['ROLE']").payload == "'analyst'"

    # A fresh deployment starts clean: no variables from earlier deployments
    assert SubAgent.from_spec(spec).tool_functions['ExecuteCode'](code='worker').status == 'error'

    print('subagent tool isolation passed')


if __name__ == '__main__':
    main()
