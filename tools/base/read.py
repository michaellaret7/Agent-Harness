"""Read a file from the local filesystem with line numbers."""
from __future__ import annotations

from pathlib import Path

DEFAULT_LIMIT = 2000
MAX_LINE_CHARS = 2000


def read(file_path: str, offset: int = 0, limit: int = DEFAULT_LIMIT) -> str:
    target = Path(file_path).expanduser().resolve()
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


tool = {
    'name': 'read',
    'description': (
        'Read a file from the local filesystem. Output uses cat -n format with '
        'line numbers starting at 1. Default reads up to 2000 lines from the start.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'file_path': {
                'type': 'string',
                'description': 'Absolute or relative path to the file.',
            },
            'offset': {
                'type': 'integer',
                'description': '0-based line number to start reading from. Default 0.',
            },
            'limit': {
                'type': 'integer',
                'description': 'Number of lines to read. Default 2000.',
            },
        },
        'required': ['file_path'],
    },
    'function': read,
}
