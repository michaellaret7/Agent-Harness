"""Replace a literal string in a file."""
from __future__ import annotations

from tools.helpers.paths import resolve_path


def edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    target = resolve_path(file_path)
    if not target.is_file():
        return f'error: not a file: {file_path!r}'
    if old_string == new_string:
        return 'error: old_string and new_string are identical'

    text = target.read_text(encoding='utf-8')
    count = text.count(old_string)

    if count == 0:
        return f'error: old_string not found in {file_path!r}'
    if count > 1 and not replace_all:
        return (
            f'error: old_string occurs {count} times in {file_path!r} — '
            'add more surrounding context to make it unique, or pass replace_all=true'
        )

    new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
    target.write_text(new_text, encoding='utf-8')
    replaced = count if replace_all else 1
    return f'replaced {replaced} occurrence(s) in {target}'


tool = {
    'name': 'EditFile',
    'description': (
        'Replace a literal string in a file. Fails if old_string is not found, '
        'or if it occurs more than once and replace_all is false.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'file_path': {
                'type': 'string',
                'description': 'Absolute or relative path to the file.',
            },
            'old_string': {
                'type': 'string',
                'description': 'Exact text to replace.',
            },
            'new_string': {
                'type': 'string',
                'description': 'Text to replace it with.',
            },
            'replace_all': {
                'type': 'boolean',
                'description': 'Replace every occurrence instead of requiring uniqueness. Default false.',
            },
        },
        'required': ['file_path', 'old_string', 'new_string'],
    },
    'function': edit,
}
