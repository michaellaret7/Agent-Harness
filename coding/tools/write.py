"""Write content to a file on the local filesystem."""
from __future__ import annotations

from typing import Annotated

from agent.decorator import Param, agent_tool
from coding.tools.helpers.paths import resolve_path


@agent_tool(name='WriteFile')
def write(
    file_path: Annotated[str, Param(description='Absolute or relative path to the file.')],
    content: Annotated[str, Param(description='Full file contents to write.')],
) -> str:
    """
    Write content to a file on the local filesystem. Overwrites any existing
    file at that path; creates parent directories as needed.
    """
    target = resolve_path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return f'wrote {len(content)} chars to {target}'
