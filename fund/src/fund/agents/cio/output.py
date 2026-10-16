"""The CIO's answer: target weights for a stock-and-ETF portfolio built to beat SPY.

Every weight is a percent of the total portfolio, and together they sum to 100:

    SectorAllocation
    ├─ sectors[11]       US equity targets, each = core + tactical   (≤ MAX_SECTOR each)
    ├─ fixed_income[]    bond buckets                                 (≤ MAX_FIXED_INCOME in total)
    ├─ international[]   regions                                      (≤ MAX_INTERNATIONAL in total)
    └─ cash_weight       fixed at CASH_WEIGHT

The weights come from the CIO's conviction, not from SPY's; `spy_weight` is kept only as the yardstick
that shows how large each bet against the benchmark is. Theme, basket, and sector ETFs fill the sector
targets later; they get no target of their own. The run ends only through an accepted SubmitResult:
the schema checks shape, and `check_allocation` checks the arithmetic and the fund's limits.
"""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

# FMP's sector names, as in SPY's sector weightings (mirrored in the skill's scripts/sectors.py)
Sector = Literal[
    'Technology',
    'Financial Services',
    'Communication Services',
    'Healthcare',
    'Consumer Cyclical',
    'Industrials',
    'Consumer Defensive',
    'Energy',
    'Utilities',
    'Basic Materials',
    'Real Estate',
]
SECTORS: tuple[str, ...] = get_args(Sector)

# Mirrored in the skill's scripts/sleeves.py
BondBucket = Literal['Short Treasuries', 'Intermediate Treasuries', 'Long Treasuries', 'TIPS', 'Investment-grade credit', 'High yield']
Region = Literal['Developed ex-US', 'Emerging markets']

# Fund limits, in percent of the total portfolio
MAX_SECTOR = 30.0
MAX_FIXED_INCOME = 20.0
MAX_INTERNATIONAL = 15.0
CASH_WEIGHT = 2.0  # fixed cash buffer
TOLERANCE = 0.1  # rounding slack on every sum
MIN_SOURCES = 60  # distinct web sources behind the allocation


class Source(BaseModel):
    title: str
    url: str = Field(description='The page you read, as returned by WebSearch or WebExtract.')
    publisher: str
    date: str = Field(description='Publication date, YYYY-MM-DD.')
    takeaway: str = Field(description='What this source contributed to the allocation, in one sentence.')


class Case(BaseModel):
    thesis: str = Field(description='Why this target: the macro and data evidence, with numbers.')
    risks: str = Field(description='What would make the call wrong.')
    review_trigger: str = Field(description='A measurable condition that forces a re-allocation.')


class SectorWeight(Case):
    sector: Sector
    weight: float = Field(ge=0, le=100, description=f'Target weight, percent of the total portfolio (at most {MAX_SECTOR:g}).')
    core_weight: float = Field(ge=0, le=100, description='Part of `weight` held as long-term core positions (percent of the portfolio).')
    tactical_weight: float = Field(ge=0, le=100, description='Part of `weight` reserved for shorter-term trades within the sector (percent of the portfolio).')
    spy_weight: float = Field(ge=0, le=100, description="Reference only: the sector's current weight in SPY (percent), from FMP etf/sector-weightings. Never a starting point for `weight`.")
    themes: list[str] = Field(description="Themes or sub-industries to favor inside this sector's target (e.g. 'semiconductors'); categories, never tickers.")


class BondWeight(Case):
    bucket: BondBucket
    weight: float = Field(ge=0, le=100, description='Target weight, percent of the total portfolio.')


class RegionWeight(Case):
    region: Region
    weight: float = Field(ge=0, le=100, description='Target weight, percent of the total portfolio.')


class SectorAllocation(BaseModel):
    macro_regime: str = Field(description='Growth and inflation direction, policy, and curve shape, with the evidence.')
    macro_thesis: str = Field(description='The macro view for the next 12-24 months and the data behind it.')
    portfolio_thesis: str = Field(description='How these targets are expected to beat SPY: the biggest bets against the benchmark and why they should pay.')
    sleeve_rationale: str = Field(description='Why bonds and international are held at their totals (or not at all), given that SPY is all US equity.')
    sectors: list[SectorWeight] = Field(description='Exactly one target for each of the 11 sectors (0 is allowed).')
    fixed_income: list[BondWeight] = Field(description='The bond buckets held; empty for none.')
    international: list[RegionWeight] = Field(description='The international regions held; empty for none.')
    cash_weight: float = Field(ge=0, le=100, description=f'Cash buffer, percent of the total portfolio (always exactly {CASH_WEIGHT:g}).')
    key_risks: list[str] = Field(description='Portfolio-level risks to the allocation.')
    research_sources: list[Source] = Field(description=f'Every web source the allocation relies on; at least {MIN_SOURCES} distinct URLs.')


#     ================================
# --> Helper funcs
#     ================================


def _check_sleeve(name: str, names: list[str], weights: list[float], cap: float) -> str | None:
    """An optional sleeve: no entry listed twice, and its total within the cap."""
    if len(set(names)) != len(names):
        return f'{name} lists the same entry more than once'

    total = sum(weights)

    if total > cap + TOLERANCE:
        return f'{name} totals {total:.2f}% of the portfolio, above the {cap:g}% limit'

    return None


def _check_sector(entry: SectorWeight) -> str | None:
    """Within the sector cap, and core + tactical equals the target."""
    if entry.weight > MAX_SECTOR + TOLERANCE:
        return f'{entry.sector}: target {entry.weight}% is above the {MAX_SECTOR:g}% per-sector limit'

    if abs(entry.core_weight + entry.tactical_weight - entry.weight) > TOLERANCE:
        return f'{entry.sector}: core_weight + tactical_weight must equal weight ({entry.weight})'

    return None


#     ================================
# --> Verifier
#     ================================


def check_allocation(allocation: SectorAllocation) -> str | None:
    """Return the first rule the allocation breaks, or None when it is acceptable."""
    urls = {source.url.strip().rstrip('/') for source in allocation.research_sources}

    if len(urls) < MIN_SOURCES:
        return f'research_sources has {len(urls)} distinct URLs; the allocation needs at least {MIN_SOURCES} from the web research'

    names = [entry.sector for entry in allocation.sectors]
    missing = set(SECTORS) - set(names)

    if missing or len(names) != len(SECTORS):
        return f'sectors must list each of the 11 sectors exactly once; missing {sorted(missing)}, got {len(names)} entries'

    if abs(allocation.cash_weight - CASH_WEIGHT) > TOLERANCE:
        return f'cash_weight must be {CASH_WEIGHT:g}% of the portfolio, got {allocation.cash_weight}'

    for problem in (
        _check_sleeve('fixed_income', [b.bucket for b in allocation.fixed_income], [b.weight for b in allocation.fixed_income], MAX_FIXED_INCOME),
        _check_sleeve('international', [r.region for r in allocation.international], [r.weight for r in allocation.international], MAX_INTERNATIONAL),
        *(_check_sector(entry) for entry in allocation.sectors),
    ):
        if problem is not None:
            return problem

    total = (
        sum(entry.weight for entry in allocation.sectors)
        + sum(bucket.weight for bucket in allocation.fixed_income)
        + sum(region.weight for region in allocation.international)
        + allocation.cash_weight
    )

    if abs(total - 100) > TOLERANCE:
        return f'all targets sum to {total:.2f}% of the portfolio, not 100'

    return None
