"""Return a tree view of files and directories, like the `tree` command."""
from __future__ import annotations

from pathlib import Path

# Heavy or noisy directories — skip outright so the tree stays readable.
SKIP = {
    '.venv', '__pycache__', '.git', '.claude', 'node_modules', 'models',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.idea', '.vscode',
    'dist', 'build', '.next',
}

MAX_LINES = 200
MAX_DEPTH = 5


def tree(path: str = '.') -> str:
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        return f'error: not a directory: {path!r}'

    lines: list[str] = [target.name + '/']
    _walk(target, '', lines, depth=0)

    if len(lines) > MAX_LINES:
        extra = len(lines) - MAX_LINES
        lines = lines[:MAX_LINES] + [f'... [truncated; {extra} more entries]']
    return '\n'.join(lines)


def _walk(dir_path: Path, prefix: str, lines: list[str], depth: int) -> None:
    if depth >= MAX_DEPTH:
        return
    try:
        entries = sorted(
            (e for e in dir_path.iterdir() if e.name not in SKIP),
            key=lambda p: (p.is_file(), p.name.lower()),
        )
    except PermissionError:
        return

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = '└── ' if is_last else '├── '
        suffix = '/' if entry.is_dir() else ''
        lines.append(prefix + connector + entry.name + suffix)
        if entry.is_dir():
            extension = '    ' if is_last else '│   '
            _walk(entry, prefix + extension, lines, depth + 1)


tool = {
    'name': 'Tree',
    'description': (
        'Return a tree view of files and directories under a folder, like the '
        'output of the `tree` command. Hidden and heavy directories (.venv, '
        '__pycache__, .git, models, node_modules, etc.) are skipped.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'path': {
                'type': 'string',
                'description': 'Absolute or relative folder path. Defaults to "." (current directory).',
            },
        },
        'required': [],
    },
    'function': tree,
}
