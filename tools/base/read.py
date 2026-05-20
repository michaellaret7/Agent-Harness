"""Read a file from the local filesystem with line numbers."""
from __future__ import annotations

from typing import Annotated

from agent.decorator import Param, agent_tool
from tools.helpers.paths import resolve_path

DEFAULT_LIMIT = 2000
MAX_LINE_CHARS = 2000


@agent_tool(name='ReadFile')
def read(
    file_path: Annotated[str, Param(description='Absolute or relative path to the file.')],
    offset: Annotated[int, Param(description='0-based line number to start reading from. Default 0.')] = 0,
    limit: Annotated[int, Param(description='Number of lines to read. Default 2000.')] = DEFAULT_LIMIT,
) -> str:
    """
    Read a file from the local filesystem. Output uses cat -n format with
    line numbers starting at 1. Default reads up to 2000 lines from the start.
    """
    target = resolve_path(file_path)
    if not target.is_file():
        return f'error: not a file: {file_path!r}'
    try:
        lines = target.read_text(encoding='utf-8').splitlines()
    except UnicodeDecodeError:
        return f'error: {file_path!r} is not a UTF-8 text file'

    selected = lines[offset:offset + limit]
    if not selected:
        return '[empty file or out-of-range offset]'

    formatted = []
    for i, line in enumerate(selected, start=offset + 1):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + '...'
        formatted.append(f'{i:>6}\t{line}')
    return '\n'.join(formatted)
