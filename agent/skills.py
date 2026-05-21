"""Disk-scanned skill loader.

Implements Level 1 (frontmatter listing for the system prompt) of the
progressive-disclosure model. Level 2 (the SKILL.md body) is loaded lazily
on demand by the `Skill` tool; Level 3 (references/, scripts/, assets/) is
left to ReadFile / Grep / Bash acting on the skill's base directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILL_FILE = 'SKILL.md'
MAX_DESC_CHARS = 250


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    root: Path


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a markdown file with leading `---` YAML frontmatter.

    Extracts top-level scalar string keys only — nested blocks (e.g. `metadata:`)
    are skipped. Returns ({}, text) when no frontmatter block is present.
    """
    if not text.startswith('---'):
        return {}, text

    end = text.find('\n---', 3)

    if end == -1:
        return {}, text

    raw = text[3:end].lstrip('\n')

    body_start = end + len('\n---')

    if body_start < len(text) and text[body_start] == '\n':
        body_start += 1

    body = text[body_start:]

    fields: dict[str, str] = {}

    for line in raw.splitlines():
        if line.startswith((' ', '\t')) or ':' not in line:
            continue

        key, _, value = line.partition(':')

        value = value.strip().strip('"').strip("'")

        if value:
            fields[key.strip()] = value

    return fields, body


def load_skills(root: Path) -> list[Skill]:
    """Scan `<root>/*/SKILL.md` and return one Skill per valid bundle.

    Only parses frontmatter — the body is read on demand by the `Skill` tool.
    """
    if not root.is_dir():
        return []

    skills: list[Skill] = []

    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_md = skill_dir / SKILL_FILE

        if not skill_md.is_file():
            continue

        try:
            text = skill_md.read_text(encoding='utf-8')

        except (OSError, UnicodeDecodeError):
            continue

        fields, _ = parse_frontmatter(text)

        name = fields.get('name') or skill_dir.name
        description = fields.get('description', '').strip()

        skills.append(Skill(
            name=name,
            description=description,
            root=skill_dir.resolve(),
        ))

    return skills


def format_skill_listing(skills: list[Skill]) -> str:
    """Render skills as a Level-1 listing for system-prompt injection."""
    if not skills:
        return ''

    lines = ['<skills>']

    for s in skills:
        desc = s.description

        if len(desc) > MAX_DESC_CHARS:
            desc = desc[:MAX_DESC_CHARS - 1] + '…'

        lines.append(f'- {s.name}: {desc}' if desc else f'- {s.name}')

    lines.append('</skills>')

    return '\n'.join(lines)
