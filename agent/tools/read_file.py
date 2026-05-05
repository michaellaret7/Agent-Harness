"""Read a UTF-8 text file from inside the project directory."""
from __future__ import annotations

from pathlib import Path

# agent/tools/read_file.py -> agent/tools -> agent -> project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Cap output so a huge file can't blow the model's context window.
MAX_CHARS = 16000

def read_file(path: str) -> str:
    target = (PROJECT_ROOT / path).resolve()
    # Reject ".." escapes and absolute paths that resolve outside the project.
    if not target.is_relative_to(PROJECT_ROOT):
        return f'error: path {path!r} is outside the project directory'
    if not target.is_file():
        return f'error: not a file: {path!r}'
    try:
        text = target.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return f'error: {path!r} is not a UTF-8 text file'
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + f'\n\n... [truncated; {len(text) - MAX_CHARS} more chars]'
    return text


tool = {
    'name': 'read_file',
    'description': (
        'Read a UTF-8 text file from the project directory and return its '
        'contents. Output is truncated past 16000 characters.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'path': {
                'type': 'string',
                'description': 'Path relative to the project root, e.g. "agent/agent.py" or "pyproject.toml".',
            },
        },
        'required': ['path'],
    },
    'function': read_file,
}
