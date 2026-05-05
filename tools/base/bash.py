"""Execute a bash command and return combined stdout/stderr.

Resolves a real bash binary at import time so the same POSIX command works on
Windows (Git Bash) and Unix. Avoids `shell=True`, which would silently dispatch
to cmd.exe on Windows — a footgun when the tool is named `bash`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

MAX_CHARS = 16000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


def _resolve_bash() -> str | None:
    # 1. Explicit override.
    override = os.environ.get('BASH_PATH')
    if override and Path(override).is_file():
        return override

    # 2. On Windows, prefer Git Bash. `shutil.which("bash")` may return
    # System32\bash.exe (WSL), which sees a different filesystem and can
    # prompt for setup — not what callers want.
    if sys.platform == 'win32':
        for candidate in (
            r'C:\Program Files\Git\bin\bash.exe',
            r'C:\Program Files\Git\usr\bin\bash.exe',
            r'C:\Program Files (x86)\Git\bin\bash.exe',
        ):
            if Path(candidate).is_file():
                return candidate

    # 3. Generic lookup.
    return shutil.which('bash')


_BASH = _resolve_bash()


def bash(command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    if _BASH is None:
        return (
            'error: bash not found. Install Git for Windows '
            '(https://git-scm.com/download/win) or set BASH_PATH to a bash binary.'
        )

    timeout = min(max(1, timeout), MAX_TIMEOUT)
    try:
        result = subprocess.run(
            [_BASH, '-c', command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f'error: command timed out after {timeout}s'

    output = (result.stdout or '') + (result.stderr or '')
    if result.returncode != 0:
        output += f'\n\n[exit code {result.returncode}]'

    if len(output) > MAX_CHARS:
        output = output[:MAX_CHARS] + f'\n\n... [truncated; {len(output) - MAX_CHARS} more chars]'
    return output or '[no output]'


tool = {
    'name': 'bash',
    'description': (
        'Execute a bash command and return combined stdout/stderr. Runs in real '
        'bash on every platform (Git Bash on Windows), so POSIX syntax works '
        'everywhere — use forward slashes and `/c/...`-style paths on Windows. '
        'Default timeout is 120 seconds (max 600).'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'command': {
                'type': 'string',
                'description': 'The bash command to execute.',
            },
            'timeout': {
                'type': 'integer',
                'description': 'Timeout in seconds. Default 120, max 600.',
            },
        },
        'required': ['command'],
    },
    'function': bash,
}
