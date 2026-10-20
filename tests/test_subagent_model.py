"""Sub-agent model contracts. Run: uv run --all-packages pytest tests/test_subagent_model.py

`SandboxConfig(recursive_agents=True, subagent_model=...)` names the model in the ExecuteCode
description's sub-agent examples and asks for it on every sub-agent. It is a prompt-level
instruction: nothing in the kernel enforces it.
"""
from __future__ import annotations

import pytest

from agent_harness.base_tools.code_execution.config import SandboxConfig
from agent_harness.base_tools.code_execution.tool import execute_code_tool

MODEL = 'deepseek/deepseek-v4.1-flash'


#     ================================
# --> Contract tests
#     ================================


def test_description_names_the_subagent_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every sub-agent example uses the configured model, and the note asks for it."""
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only')

    description = execute_code_tool(SandboxConfig(recursive_agents=True, subagent_model=MODEL))['description']

    assert description.count(f"model='{MODEL}'") == 3  # two examples + the instruction
    assert f"Use model='{MODEL}' for every sub-agent." in description
    assert '<model id>' not in description


def test_without_a_model_the_examples_keep_the_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no model configured, the choice stays with the model code."""
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only')

    description = execute_code_tool(SandboxConfig(recursive_agents=True))['description']

    assert "model='<model id>'" in description
    assert 'for every sub-agent.' not in description


def test_model_without_recursive_agents_fails_fast() -> None:
    """A sub-agent model means nothing without sub-agents, so it is a configuration error."""
    with pytest.raises(ValueError, match='recursive_agents'):
        execute_code_tool(SandboxConfig(subagent_model=MODEL))
