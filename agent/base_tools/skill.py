"""Skill tool: Level-2 loader for SKILL.md bundles on disk.

Reads one SKILL.md body on demand and prepends the bundle's base directory so
the model can pull references and run scripts via ReadFile / Grep / Bash —
those existing tools are Level 3.

The skill registry is captured at `Agent.__init__` time (it depends on disk
state) and injected into the hidden `_skills_map` parameter via `bind_tool`
(see `agent.py`). That underscore-prefixed param is hidden from the generated
JSON Schema by the decorator's convention, so the LLM never sees it.
"""
from __future__ import annotations

from typing import Annotated

from agent.decorator import Param, agent_tool
from agent.skills import SKILL_FILE, Skill, parse_frontmatter


@agent_tool(name='Skill')
def load_skill(
    skill: Annotated[str, Param(description='Name of the skill to load (from the <skills> listing).')],
    _skills_map: dict[str, Skill] | None = None,
) -> str:
    """
    Load the full instructions for a skill listed in the <skills> block of
    the system prompt. CALL THIS FIRST whenever a skill's description matches
    the user's request — before any other tool. Skills are pre-built workflows
    that beat ad-hoc exploration. Returns the SKILL.md body and the skill's
    base directory; follow those instructions on the next turn, and use
    ReadFile, Grep, or Bash against the base directory to pull references or
    run scripts.
    """
    by_name = _skills_map or {}
    match = by_name.get(skill)

    if match is None:
        available = ', '.join(sorted(by_name)) or '(none)'
        return f'error: unknown skill {skill!r}. Available: {available}'

    skill_md = match.root / SKILL_FILE

    try:
        text = skill_md.read_text(encoding='utf-8')

    except (OSError, UnicodeDecodeError) as e:
        return f'error: cannot read {skill_md}: {e}'

    _, body = parse_frontmatter(text)

    return (
        f'Base directory for this skill: {match.root}\n\n'
        f'{body.rstrip()}\n'
    )
