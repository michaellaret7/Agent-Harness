"""The Technology pod's four industry analysts: one shared role prompt, one agent and workspace per industry.

    ~/.agent_harness/
    ├── workspaces/tech-<slug>-analyst/sandbox/  ExecuteCode working dir (universe, research notes)
    └── memory/tech-<slug>-analyst.sqlite        long-term memory: theses sent, passes, lessons

    COVERAGE (4 industries) ──► _build_analyst ──► ANALYSTS: ((role, agent), …)
                                  prompt.md + <coverage> block, FUND_SANDBOX (FMP only, no Alpaca keys)

Each analyst researches its industry, sends the PM new ideas, position updates, and watchlist names,
and follows the fund's trades with GetTrades (bound by the org at registration). Analysts never trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_harness import Agent, AgentWorkspace, CompactionConfig

from fund.agents.sandbox import FUND_MODEL, FUND_SANDBOX

PROMPT = (Path(__file__).parent / 'prompt.md').read_text(encoding='utf-8').strip()


@dataclass(frozen=True)
class Coverage:
    """One analyst's industry: its name as the PM uses it, the FMP profile labels that make up its
    universe, and the industry-specific signals worth tracking."""

    slug: str
    industry: str
    fmp_industries: tuple[str, ...]
    signals: str


COVERAGE = (
    Coverage(
        'software', 'Software', ('Software - Application', 'Software - Infrastructure'),
        'RPO and billings growth, net revenue retention, Rule of 40, seat versus consumption trends, '
        'and evidence that AI products are monetizing.',
    ),
    Coverage(
        'semiconductors', 'Semiconductors & Semiconductor Equipment', ('Semiconductors',),
        'TSMC monthly revenue (around the 10th of each month), hyperscaler capex guidance, memory pricing, '
        'and equipment orders and book-to-bill.',
    ),
    Coverage(
        'comms-equipment', 'Communications Equipment', ('Communication Equipment',),
        'carrier and cloud/AI networking capex, high-speed optics demand (800G and up), and order backlogs.',
    ),
    Coverage(
        'hardware', 'Technology Hardware, Storage & Peripherals', ('Computer Hardware', 'Consumer Electronics'),
        'PC and smartphone shipment data, AI server demand, memory and component costs, and product cycles.',
    ),
)


#     ================================
# --> Helper funcs
#     ================================


def _coverage_block(coverage: Coverage) -> str:
    """The analyst's industry, rendered into its system prompt."""
    labels = ', '.join(f'"{label}"' for label in coverage.fmp_industries)

    return (
        '<coverage>\n'
        f'Your industry: {coverage.industry}.\n'
        f'FMP `industry` labels in your universe: {labels}.\n'
        f'Industry signals worth tracking: {coverage.signals}\n'
        '</coverage>'
    )


def _build_analyst(coverage: Coverage) -> tuple[str, Agent]:
    """One analyst: its agent, attached to its own workspace (sandbox, skills, memory)."""
    workspace = AgentWorkspace(
        f'tech-{coverage.slug}-analyst',
        sandbox=FUND_SANDBOX,
        memory=True,
        auto_memory_recall=True,
        curate_memory=True,
    )

    agent = Agent(
        system=f'{PROMPT}\n\n{_coverage_block(coverage)}',
        model=FUND_MODEL,
        max_iters=200,
        reasoning_effort='high',
        # Archived inside the sandbox, so the analyst can open a summary's `(archive #n)` citation with ExecuteCode
        compaction=CompactionConfig(context_window=272_000, archive_dir=workspace.sandbox_dir / 'history'),
    )

    workspace.attach(agent)

    return f'{coverage.industry} Analyst (Technology Pod)', agent


# One analyst per industry per process: registered once by the org, then run for every message it receives
ANALYSTS: tuple[tuple[str, Agent], ...] = tuple(_build_analyst(coverage) for coverage in COVERAGE)
