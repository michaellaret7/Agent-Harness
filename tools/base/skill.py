"""Skill tool: Level-2 loader for SKILL.md bundles on disk.

The tool itself is dumb on purpose. It reads one SKILL.md body and prepends the
bundle's base directory so the model can pull references and run scripts on
demand using ReadFile / Grep / Bash — those existing tools are Level 3.
"""
from __future__ import annotations

from agent.skills import Skill


def make_skill_tool(skills: list[Skill]) -> dict:
    """Build a Skill tool definition bound to the given skill registry."""
    by_name = {s.name: s for s in skills}

    def skill_fn(skill: str) -> str:
        match = by_name.get(skill)

        if match is None:
            available = ', '.join(sorted(by_name)) or '(none)'

            return f'error: unknown skill {skill!r}. Available: {available}'

        return (
            f'Base directory for this skill: {match.root}\n\n'
            f'{match.body.rstrip()}\n'
        )

    return {
        'name': 'Skill',
        'description': (
            'Load the full instructions for a skill listed in the <skills> '
            'block of the system prompt. CALL THIS FIRST whenever a skill\'s '
            'description matches the user\'s request — before any other tool. '
            'Skills are pre-built workflows that beat ad-hoc exploration. '
            'Returns the SKILL.md body and the skill\'s base directory; follow '
            'those instructions on the next turn, and use ReadFile, Grep, or '
            'Bash against the base directory to pull references or run scripts.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'skill': {
                    'type': 'string',
                    'description': 'Name of the skill to load (from the <skills> listing).',
                },
            },
            'required': ['skill'],
        },
        'function': skill_fn,
    }
