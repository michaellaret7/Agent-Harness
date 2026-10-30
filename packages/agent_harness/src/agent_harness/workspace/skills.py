"""Index reusable skills stored in the sandbox.

    skills/curated/<name>/   recopied from the source on every attach
    skills/learned/<name>/   agent-managed; preserved by the harness

Each skill has SKILL.md plus optional references/, scripts/, and assets/.
The agent reads and writes them through ExecuteCode. `skills_prompt` defines
the format; SkillsIndex adds names, tiers, descriptions, and format warnings
(including unlinked files) to the system prompt.
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
Skills are reusable procedures (how to do something), stored as folders in your working directory.
The `<skills>` block lists them as of the start of this session, each tagged with its tier.
Skills you write now are not listed until the next session. Read and write them with ExecuteCode.

Tiers:
{tiers}

## Layout

A skill is a folder, not a single file. Only SKILL.md is required; add the other parts when the
skill needs them:

```
<name>/
├── SKILL.md        overview: when to use it, the steps, and a link to every other file
├── references/     detail needed only for some steps: schemas, API notes, edge cases, worked examples
├── scripts/        Python for deterministic work you would otherwise rewrite each time
└── assets/         templates and data files the output is built from
```

The layers load in order, so each costs context only when it is needed:
1. The `<skills>` block shows only the name and description. The description decides whether the
   skill is ever opened, so it must say what the skill does and when to use it.
2. SKILL.md is read in full when the skill is used. Keep it under about 100 lines: the steps, plus
   one line per bundled file saying when to open it.
3. A reference is read only when a step points to it. A script is run, never read, so only its
   output costs context.

Every SKILL.md must start with exactly this frontmatter:

```markdown
---
name: <lowercase-slug, identical to the folder name>
description: <one line: what it does and when to use it>
---

## Steps
1. ... Run `scripts/fetch_prices.py` (`fetch_prices(tickers) -> DataFrame`).
2. ... For the column definitions, read `references/fields.md`.

## Files
- `references/fields.md`: field meanings and units; read when interpreting the raw columns.
- `scripts/fetch_prices.py`: `fetch_prices(tickers) -> DataFrame`; run it, do not rewrite it.
```

Writing the parts:
- Put a step's code in `scripts/` when it is longer than a few lines or must run the same way every
  time. A script defines functions with type hints and a one-line docstring, handles its own errors
  with clear messages, and does not run anything on import.
- Move detail out of SKILL.md into `references/` once the overview passes about 100 lines, or when only
  some uses need it. One topic per reference file, with a descriptive name (`fields.md`, not
  `notes.md`). Give a reference over 100 lines a contents list at the top.
- Link every bundled file from SKILL.md by its path relative to the skill folder. A file SKILL.md
  does not link cannot be found. Do not chain files: a reference never sends you to another file.

## Using a skill

1. Pick it from the `<skills>` block by its description, then read its SKILL.md in full.
2. Follow the steps in order. Open a reference only when the step you are on points to it, and read
   that file in full.
3. Run a script instead of reading it or rewriting its logic:
   `fns = runpy.run_path('skills/<tier>/<name>/scripts/<file>.py')`, then call `fns['<function>'](...)`.
   Read a script's source only when it fails or you are updating it.
4. Build output from the files in `assets/` when a step names one, rather than recreating them.

When to write a learned skill:
- Most tasks should end without writing one. Skills are for lasting procedures, not a log of each task.
- Write one only when you worked out a non-obvious procedure that would have saved you real effort had
  you known it at the start, and that applies beyond this one task.
- First check the `<skills>` list. If an existing skill already covers it, follow it unchanged, or update
  that learned skill if you found a real gap. Never create a second skill for the same topic.
- Do not write a skill for a one-off fact, a result, or anything an existing skill already says.

Rules:
- One skill per procedure. When a skill grows, move detail into its `references/` and code into its
  `scripts/`; do not split one procedure into several skills.
- To update a learned skill, edit the file that holds the change and keep SKILL.md's links current.
- A learned skill marked INVALID in the `<skills>` block must be fixed as the message says.
</skills_protocol>
"""

CURATED_TIER = """- `skills/curated/<name>/SKILL.md`: reviewed by a human. Follow them. Never edit them: changes are discarded.
- `skills/learned/<name>/SKILL.md`: yours. Create and improve them freely.
- When a curated and a learned skill conflict, the curated one wins.
- If a curated skill seems wrong or incomplete, write a learned skill named `<curated-name>-notes`
  explaining what you found, so a human can review it."""

LEARNED_ONLY_TIER = '- `skills/learned/<name>/SKILL.md`: yours. Create and improve them freely.'


#     ================================
# --> Helper funcs
#     ================================


def skills_prompt(curated: bool) -> str:
    """The skills protocol, describing the curated tier only when a curated source exists."""
    return SKILLS_PROMPT.format(tiers=CURATED_TIER if curated else LEARNED_ONLY_TIER)


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


def _unlinked_files(skill_dir: Path, text: str) -> list[str]:
    """Bundled files whose path relative to the skill folder never appears in SKILL.md's text."""
    unlinked: list[str] = []

    for path in sorted(skill_dir.rglob('*')):
        relative = path.relative_to(skill_dir).as_posix()

        # Interpreter caches and hidden files are not part of the skill
        if not path.is_file() or relative == 'SKILL.md' or '__pycache__' in path.parts or path.name.startswith('.'):
            continue

        if relative not in text:
            unlinked.append(relative)

    return unlinked


def _index_line(skill_file: Path, tier: str) -> str:
    """One `<skills>` line for a skill: its tier and description, or why its format is invalid."""
    folder = skill_file.parent.name
    text = skill_file.read_text(encoding='utf-8')
    fields = _frontmatter(text)
    unlinked = _unlinked_files(skill_file.parent, text)
    problem: str | None = None

    if fields is None:
        problem = 'missing --- frontmatter block'

    elif not SLUG.fullmatch(folder) or fields.get('name') != folder:
        problem = 'name must be a lowercase slug matching the folder'

    elif not fields.get('description'):
        problem = 'missing description'

    elif unlinked:
        problem = f'SKILL.md does not link {", ".join(unlinked)}; link each file or delete it'

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
