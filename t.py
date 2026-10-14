"""Scratch script: watch skills, memory and automatic memory recall work across three runs.

Run it three times. Each run is a new process with a fresh Agent, so anything
the agent knows about earlier runs came from its workspace (skills on disk,
memories in SQLite), never from chat history:

    run 1  MSFT data pull   user states preferences   -> agent Remembers them + FMP quirks it hits
    run 2  MSFT DCF         <memories> shows run 1's preferences, curated dcf-valuation skill applies
    run 3  AAPL vs MSFT     <memories> shows run 2's MSFT result, learned skills (if any) are listed

Start over by deleting ~/.agent_harness/workspaces/memory-demo and ~/.agent_harness/memory/memory-demo.sqlite.
"""

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import httpx

from dotenv import load_dotenv

from agent_harness import Agent, AgentWorkspace

load_dotenv()

# Anchored to this file so the script works from any working directory
CURATED_SKILLS = Path(__file__).parent / 'examples' / 'skills'

TASKS = {
    1: (
        'Pull MSFT annual revenue, free cash flow and total debt for the last 4 fiscal years from FMP and '
        'summarize the trend. Some standing preferences of mine for all future work: always show numbers in '
        'tables (in $B, one decimal), use a 5.0% equity risk premium in any cost-of-equity calculation, and '
        'end every analysis with a one-line verdict.'
    ),
    2: 'Run a DCF valuation of MSFT following the dcf-valuation skill.',
    3: 'Run the same DCF on AAPL and compare its upside or downside with MSFT.',
}


#     ================================
# --> Helper funcs
#     ================================


def next_run_number(root: Path) -> int:
    """Count runs in a small file in the workspace, so each invocation picks the next task."""
    counter = root / 'demo_run.txt'
    run = int(counter.read_text()) + 1 if counter.exists() else 1

    counter.write_text(str(run))

    return run


def print_memories(store_path: Path, title: str) -> None:
    """Dump every stored memory with its ranking signals (scratch-script peek; the agent never does this)."""
    print(f'\n=== {title} ===')

    if not store_path.exists():
        print('  (no store yet)')
        return

    with sqlite3.connect(store_path) as db:
        rows = db.execute('SELECT id, kind, importance, use_count, content FROM memories ORDER BY id').fetchall()

    if not rows:
        print('  (empty)')

    for memory_id, kind, importance, use_count, content in rows:
        print(f'  [{memory_id}] {kind:<10} importance={importance} uses={use_count}  {content}')


#     ================================
# --> Market spot provider
#     ================================


class MarketSpot:
    """Dynamic context provider: SPY and VIX spot prices from FMP, refetched at most once a minute.

    The renderer calls providers before every model call, so the TTL keeps a
    long tool loop from hitting FMP on each iteration.
    """

    URL = 'https://financialmodelingprep.com/stable/batch-quote'
    TTL_SECONDS = 60

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._fetched_at = 0.0
        self._block: str | None = None

    def __call__(self) -> str | None:
        if time.monotonic() - self._fetched_at < self.TTL_SECONDS:
            return self._block

        response = httpx.get(self.URL, params={'symbols': 'SPY,^VIX', 'apikey': self.api_key}, timeout=10)
        response.raise_for_status()

        lines = [
            f'- {quote["symbol"]}: {quote["price"]:.2f} ({quote["changePercentage"]:+.2f}% today)'
            for quote in response.json()
        ]

        self._fetched_at = time.monotonic()
        self._block = f'<market_spot as_of="{datetime.now():%Y-%m-%d %H:%M}">\n' + '\n'.join(lines) + '\n</market_spot>'

        return self._block


#     ================================
# --> Demo
#     ================================


workspace = AgentWorkspace(
    'memory-demo',
    curated_skills=CURATED_SKILLS,
    env={'FMP_API_KEY': os.environ['FMP_API_KEY']},
    packages=['pandas', 'numpy', 'requests'],
    memory=True,
    auto_memory_recall=True,
)

assert workspace.memory is not None

run = next_run_number(workspace.root)

if run not in TASKS:
    raise SystemExit(f'All {len(TASKS)} demo runs done. Delete the workspace and memory file to start over.')

task = TASKS[run]

agent = Agent(
    system=(
        '<role>\n'
        'You are an equity analyst. Use ExecuteCode with the FMP API '
        '(https://financialmodelingprep.com/stable/...) for every number. Never estimate data you can fetch.\n'
        '</role>\n\n'
        '<constraints>\n'
        '- Fetch FMP data fresh on every run straight into Python variables and work on those in the kernel.\n'
        '- Do not save API responses to files: market data goes stale.\n'
        '</constraints>'
    ),
    provider='openrouter',
    model='openai/gpt-6-sol',
)

workspace.attach(agent)

# Registered after attach, so its block follows <memories> inside <dynamic_context>
agent.add_dynamic_provider(MarketSpot(os.environ['FMP_API_KEY']))

print(f'\n######## RUN {run} of {len(TASKS)} ########')
print(f'Task: {task}')

print_memories(workspace.memory.path, 'Memories before this run')

print('\n=== Skills index in this run\'s system prompt (snapshot taken at attach) ===')
print(workspace.skills() or '  (no skills)')

answer = agent.run(task)

print('\n=== Final answer ===')
print(answer)

# Both providers cache (per user message / per minute), so this matches what the model saw at the end of the run
print('\n=== <dynamic_context> message appended to every model call ===')
print(agent.context_renderer._dynamic_context() or '  (empty)')

print_memories(workspace.memory.path, 'Memories after this run (uses = deliberate Recalls only)')

for skill in sorted((workspace.skills_dir / 'learned').glob('*/SKILL.md')):
    print(f'\n=== Learned skill (listed in the next run\'s system prompt): {skill.parent.name} ===')
    print(skill.read_text(encoding='utf-8'))

print(f'\nWorkspace: {workspace.root}')
print(f'Next: run t.py again for run {run + 1}.' if run < len(TASKS) else '\nDemo complete.')
