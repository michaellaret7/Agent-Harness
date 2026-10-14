"""Skills: reusable procedures the agent reads and writes as files in its sandbox.

Skills live in two tiers under `skills/` in the sandbox workspace:

    skills/curated/<name>/SKILL.md   human-reviewed; re-copied from the source on every attach
    skills/learned/<name>/SKILL.md   the agent's own; created and edited freely, never touched by the harness

The agent reads and writes skills with ExecuteCode like any other file; there
is no skill tool. The format is set by `SKILLS_PROMPT`, and `SkillsIndex`
enforces it softly: at attach it lists each skill with its tier and
description in the system prompt, and flags a file that breaks the format.
"""
from __future__ import annotations

import re
from pathlib import Path

# Folder names: lowercase words joined by hyphens, e.g. "dcf-valuation"
SLUG = re.compile(r'[a-z0-9]+(-[a-z0-9]+)*')

CURATED = 'curated'
LEARNED = 'learned'

SKILLS_PROMPT = """
<skills_protocol>
Skills are reusable procedures (how to do something), stored as files in your working directory.
The `<skills>` block lists them as of the start of this session, each tagged with its tier.
Skills you write now are not listed until the next session. Read and write them with ExecuteCode.

Tiers:
- `skills/curated/<name>/SKILL.md`: reviewed by a human. Follow them. Never edit them: changes are discarded.
- `skills/learned/<name>/SKILL.md`: yours. Create and improve them freely.
- When a curated and a learned skill conflict, the curated one wins.
- If a curated skill seems wrong or incomplete, write a learned skill named `<curated-name>-notes`
  explaining what you found, so a human can review it.

Every SKILL.md must use exactly this format:

```markdown
---
name: <lowercase-slug, identical to the folder name>
description: <one line: what it does and when to use it>
---

<markdown instructions>
```

When to write a learned skill:
- Most tasks should end without writing one. Skills are for lasting procedures, not a log of each task.
- Write one only when you worked out a non-obvious procedure that would have saved you real effort had
  you known it at the start, and that applies beyond this one task.
- First check the `<skills>` list. If an existing skill already covers it, follow it unchanged, or update
  that learned skill if you found a real gap. Never create a second skill for the same topic.
- Do not write a skill for a one-off fact, a result, or anything an existing skill already says.

Rules:
- Read a skill's file before following it.
- Keep each skill short and focused; split a large one into several.
- To update a learned skill, rewrite the whole file in the format above.
- A learned skill marked INVALID in the `<skills>` block must be rewritten in the correct format.
</skills_protocol>
"""


#     ================================
# --> Helper funcs
#     ================================


def _frontmatter(text: str) -> dict[str, str] | None:
    """Parse a leading `---` block of `key: value` lines; None when there is none."""
    lines = text.splitlines()

    if not lines or lines[0].strip() != '---':
        return None

    fields: dict[str, str] = {}

    for line in lines[1:]:
        if line.strip() == '---':
            return fields

        key, sep, value = line.partition(':')

        if sep:
            fields[key.strip()] = value.strip()

    return None


def _index_line(skill_file: Path, tier: str) -> str:
    """One `<skills>` line for a skill: its tier and description, or why its format is invalid."""
    folder = skill_file.parent.name
    fields = _frontmatter(skill_file.read_text(encoding='utf-8'))
    problem: str | None = None

    if fields is None:
        problem = 'missing --- frontmatter block'

    elif not SLUG.fullmatch(folder) or fields.get('name') != folder:
        problem = 'name must be a lowercase slug matching the folder'

    elif not fields.get('description'):
        problem = 'missing description'

    if problem is None and fields is not None:
        return f'- {folder} [{tier}]: {fields["description"]}'

    # A curated skill is fixed at its source by a human, not by the agent
    fix = 'rewrite it' if tier == LEARNED else 'do not edit it, note it in a learned skill'

    return f'- {folder} [{tier}]: INVALID ({problem}), {fix}'


#     ================================
# --> Index
#     ================================


class SkillsIndex:
    """Renders the `<skills>` index of the curated and learned skills under one folder."""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def __call__(self) -> str | None:
        """Render the `<skills>` block, curated first, or None when there are no skills yet."""
        lines = [
            _index_line(path, tier)
            for tier in (CURATED, LEARNED)
            for path in sorted((self.skills_dir / tier).glob('*/SKILL.md'))
        ]

        if not lines:
            return None

        return '<skills>\n' + '\n'.join(lines) + '\n</skills>'
