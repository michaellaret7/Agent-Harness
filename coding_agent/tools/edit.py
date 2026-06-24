"""Replace literal strings in a file — one or many edits, applied atomically."""
from __future__ import annotations

from typing import Annotated

from agent_harness.decorator import Param, agent_tool
from coding.tools.helpers.paths import resolve_path


@agent_tool(name='EditFile')
def edit(
    file_path: Annotated[str, Param(description='Absolute or relative path to the file.')],
    old_strings: Annotated[list[str], Param(description='A JSON array of exact strings to find. Must be a list even for a single edit — wrap one string as ["..."], do not pass a bare string. Must have the same number of items as new_strings.')],
    new_strings: Annotated[list[str], Param(description='A JSON array of replacement strings, paired by index with old_strings (new_strings[i] replaces old_strings[i]). Must be a list even for a single edit — wrap one string as ["..."]. Must have the same number of items as old_strings.')],
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

    # Guard against the common mistake of passing a bare string instead of a
    # list. We reject rather than coerce: coercing a mismatched pair (e.g. a
    # list old_strings with a bare-string new_strings) could silently apply a
    # wrong edit to the file. A loud error is cheap; a corrupted file is not.
    bad = [
        name
        for name, val in (('old_strings', old_strings), ('new_strings', new_strings))
        if isinstance(val, str)
    ]
    if bad:
        return (
            f'error: {" and ".join(bad)} must be a JSON array of strings, not a bare '
            'string. Wrap a single edit as ["..."]. old_strings and new_strings must '
            'be lists of equal length, paired by index.'
        )

    if not old_strings:
        return 'error: old_strings must not be empty'

    if len(old_strings) != len(new_strings):
        return (
            f'error: old_strings and new_strings must have the same number of '
            f'items (got {len(old_strings)} and {len(new_strings)}). Each must be a '
            f'JSON array; wrap a single edit as ["..."]. Note: if you passed bare '
            f'strings, these counts are character lengths, not item counts.'
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