"""Search file contents using a regex pattern."""
from __future__ import annotations

import re
from pathlib import Path

from tools.helpers.paths import resolve_path

MAX_MATCHES = 200
SKIP_DIRS = {
    '.venv', '__pycache__', '.git', 'node_modules', 'models',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.idea', '.vscode',
    'dist', 'build', '.next',
}


def grep(pattern: str, path: str = '.', glob: str = '*', ignore_case: bool = False) -> str:
    base = resolve_path(path)
    if not base.exists():
        return f'error: path not found: {path!r}'

    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f'error: invalid regex: {e}'

    files = [base] if base.is_file() else _iter_files(base, glob)
    matches: list[str] = []

    for file in files:
        try:
            text = file.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append(f'{file}:{lineno}:{line}')
                if len(matches) >= MAX_MATCHES:
                    matches.append(f'... [truncated at {MAX_MATCHES} matches]')
                    return '\n'.join(matches)

    return '\n'.join(matches) if matches else '[no matches]'


def _iter_files(base: Path, glob_pattern: str):
    for p in base.rglob(glob_pattern):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.parts):
            yield p


tool = {
    'name': 'Grep',
    'description': (
        'Search file contents with a regex pattern. Returns matching lines as '
        '"path:lineno:line", up to 200 matches. When path is a directory, the '
        'glob filter limits which files are scanned (default "*"). Heavy dirs '
        '(.venv, .git, node_modules, etc.) are skipped.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'pattern': {
                'type': 'string',
                'description': 'Regex pattern to search for.',
            },
            'path': {
                'type': 'string',
                'description': 'File or directory to search in. Default is the current working directory.',
            },
            'glob': {
                'type': 'string',
                'description': 'Glob filter for filenames when path is a directory, e.g. "*.py". Default "*".',
            },
            'ignore_case': {
                'type': 'boolean',
                'description': 'Case-insensitive search. Default false.',
            },
        },
        'required': ['pattern'],
    },
    'function': grep,
}
