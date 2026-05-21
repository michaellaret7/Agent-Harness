"""Path normalization for cross-platform tool calls.

The Bash tool runs in Git Bash on Windows, where paths look like `/c/Dev/foo`.
The model picks up that convention and reuses it on other filesystem tools —
but `pathlib.Path('/c/Dev/foo').resolve()` on Windows yields `C:\\c\\Dev\\foo`
(literal `\\c\\`), not the intended `C:\\Dev\\foo`. This helper rewrites the
Git Bash mount form to a native one before the path hits `pathlib`, so the
model's path convention works across every tool.
"""
from __future__ import annotations

import sys
from pathlib import Path

#     ================================
# --> Helper funcs
#     ================================


def normalize_path(path: str) -> str:
    """Rewrite Git Bash style paths to native form on Windows.

    `/c/foo/bar` → `C:/foo/bar`, `/c` → `C:/`. No-op on non-Windows or when
    the input isn't in the mount-letter form. Forward slashes in the output
    are fine — `pathlib.Path` accepts them on Windows.
    """
    if sys.platform != 'win32' or len(path) < 2 or path[0] != '/':
        return path

    if not path[1].isalpha():
        return path

    if len(path) > 2 and path[2] != '/':
        return path

    drive = path[1].upper()
    rest = path[3:] if len(path) > 3 else ''

    return f'{drive}:/{rest}' if rest else f'{drive}:/'


def resolve_path(path: str) -> Path:
    """Normalize, expand, and resolve `path` to an absolute `Path`."""
    return Path(normalize_path(path)).expanduser().resolve()
