"""Replace literal strings in a file — one or many edits, applied atomically."""
from __future__ import annotations

from typing import Annotated

from agent.decorator import Param, agent_tool
from tools.helpers.paths import resolve_path


@agent_tool(name='EditFile')
def edit(
    file_path: Annotated[str, Param(description='Absolute or relative path to the file.')],
    old_strings: Annotated[list[str], Param(description='List of exact strings to find and replace.')],
    new_strings: Annotated[list[str], Param(description='List of replacement strings, one per old_strings entry.')],
    replace_all: Annotated[bool, Param(description='For each pair, replace every occurrence instead of requiring uniqueness. Default false.')] = False,
) -> str:
    """
    Replace literal strings in a file. Supports one or many edits applied
    atomically — edits are applied in order, each seeing the result of
    previous edits. Fails if any old_string is not found, or if it occurs
    more than once and replace_all is false. All edits must succeed or the
    file is unchanged.
    """
    target = resolve_path(file_path)
    if not target.is_file():
        return f'error: not a file: {file_path!r}'

    if not old_strings:
        return 'error: old_strings must not be empty'

    if len(old_strings) != len(new_strings):
        return (
            f'error: old_strings ({len(old_strings)}) and '
            f'new_strings ({len(new_strings)}) must have the same length'
        )

    text = target.read_text(encoding='utf-8')
    skipped = 0

    for i, (old, new) in enumerate(zip(old_strings, new_strings)):
        if old == new:
            skipped += 1
            continue

        count = text.count(old)

        if count == 0:
            return f'error: old_strings[{i}] not found in {file_path!r}'

        if count > 1 and not replace_all:
            return (
                f'error: old_strings[{i}] occurs {count} times in {file_path!r} — '
                'add more surrounding context to make it unique, or pass replace_all=true'
            )

        text = text.replace(old, new) if replace_all else text.replace(old, new, 1)

    if skipped == len(old_strings):
        return f'skipped all {skipped} edits (old and new strings identical)'

    target.write_text(text, encoding='utf-8')
    applied = len(old_strings) - skipped
    return f'applied {applied} edit(s) to {target}'