"""Write content to a file on the local filesystem."""
from __future__ import annotations

from tools.helpers.paths import resolve_path


def write(file_path: str, content: str) -> str:
    target = resolve_path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return f'wrote {len(content)} chars to {target}'


tool = {
    'name': 'WriteFile',
    'description': (
        'Write content to a file on the local filesystem. Overwrites any '
        'existing file at that path; creates parent directories as needed.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'file_path': {
                'type': 'string',
                'description': 'Absolute or relative path to the file.',
            },
            'content': {
                'type': 'string',
                'description': 'Full file contents to write.',
            },
        },
        'required': ['file_path', 'content'],
    },
    'function': write,
}
