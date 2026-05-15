"""Match files by glob pattern, sorted newest-first by mtime."""
from __future__ import annotations

from pathlib import Path

from tools.helpers.paths import resolve_path

MAX_RESULTS = 200
SKIP_DIRS = {
    '.venv', '__pycache__', '.git', 'node_modules', 'models',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.idea', '.vscode',
    'dist', 'build', '.next',
}


def glob(
    pattern: str,
    path: str = '.',
    files_only: bool = True,
    limit: int = MAX_RESULTS,
) -> str:
    if not pattern or not pattern.strip():
        return "error: pattern must be a non-empty string"

    base = resolve_path(path)
    if not base.is_dir():
        return f'error: not a directory: {path!r}'

    # Cap limit defensively so a caller can't ask for a million results.
    limit = max(1, min(limit, MAX_RESULTS))

    # Collect (mtime, path) once per match to avoid repeated stat() calls
    # and to gracefully skip broken symlinks / permission errors.
    seen: set[Path] = set()
    entries: list[tuple[float, Path]] = []
    try:
        candidates = base.glob(pattern)
    except OSError as e:
        return f'error: failed to glob {pattern!r}: {e}'

    for p in candidates:
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if files_only and not p.is_file():
            continue
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved in seen:  # symlink-loop / duplicate guard
            continue
        seen.add(resolved)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        entries.append((mtime, p))

    if not entries:
        kind = 'files' if files_only else 'entries'
        return f"[no {kind} matching {pattern!r} under {base}]"

    entries.sort(key=lambda t: t[0], reverse=True)
    total = len(entries)
    shown = entries[:limit]

    lines: list[str] = []
    for _, p in shown:
        try:
            rel = p.relative_to(base)
            lines.append(str(rel))
        except ValueError:
            lines.append(str(p))  # outside base (rare with glob, but safe)

    if total > limit:
        lines.append(f'... [showing {limit} newest of {total} matches]')
    return '\n'.join(lines)


tool = {
    'name': 'Glob',
    'description': (
        'Find files matching a glob pattern (e.g. "**/*.py" or "src/*.ts"). '
        'Results are returned as paths relative to the search directory, '
        'sorted newest-first by modification time, capped at 200. Heavy dirs '
        '(.venv, .git, node_modules, etc.) are skipped.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'pattern': {
                'type': 'string',
                'description': 'Glob pattern, e.g. "**/*.py" or "src/*.ts".',
            },
            'path': {
                'type': 'string',
                'description': 'Directory to search in. Default is the current working directory.',
            },
            'files_only': {
                'type': 'boolean',
                'description': 'If true (default), only return files. Set false to include directories.',
            },
            'limit': {
                'type': 'integer',
                'description': 'Max results to return (1-200). Default 200.',
            },
        },
        'required': ['pattern'],
    },
    'function': glob,
}
