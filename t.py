"""Scratch script: an agent with a persistent workspace runs a DCF on live FMP data.

The workspace gives the agent a sandbox (FMP key + analysis libraries) and a
two-tier skill library: the human-reviewed DCF method from `examples/skills/`
(curated, read-only) and whatever the agent learns along the way (learned).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from agent_harness import Agent, AgentWorkspace

load_dotenv()

# Anchored to this file so the script works from any working directory
CURATED_SKILLS = Path(__file__).parent / 'examples' / 'skills'

workspace = AgentWorkspace(
    'analyst',
    curated_skills=CURATED_SKILLS,
    env={'FMP_API_KEY': os.environ['FMP_API_KEY']},
    packages=['pandas', 'numpy', 'scipy', 'requests', 'matplotlib'],
)

agent = Agent(
    system=(
        '<role>\n'
        'You are an equity analyst. Use ExecuteCode with the FMP API '
        '(https://financialmodelingprep.com/stable/...) for every number. Never estimate data you can fetch.\n'
        '</role>\n\n'
        '<constraints>\n'
        '- Fetch FMP data fresh on every run straight into Python variables (e.g. pandas DataFrames) '
        'and work on those in the kernel.\n'
        '- Do not save API responses to files and do not reuse data files from earlier runs: market data goes stale.\n'
        '</constraints>'
    ),
    provider='openrouter',
    model='openai/gpt-6-sol',
)

workspace.attach(agent)

answer = agent.run(
    'Run a DCF valuation of MSFT following the dcf-valuation skill, and save a chart of projected '
    'free cash flow as dcf_msft.png.'
)

print('\n=== Final answer ===')
print(answer)

print('\n=== Workspace ===')

print(workspace.root)

for path in sorted(workspace.sandbox_dir.rglob('*')):
    if path.is_file():
        print(' ', path.relative_to(workspace.root))

print('\n=== Skills index the agent will see next run ===')
print(workspace.skills())

# Learned skills are the agent's drafts: review them, and copy good ones into examples/skills/ to promote them
for skill in sorted((workspace.skills_dir / 'learned').glob('*/SKILL.md')):
    print(f'\n=== Learned skill to review: {skill.parent.name} ===')
    print(skill.read_text(encoding='utf-8'))
