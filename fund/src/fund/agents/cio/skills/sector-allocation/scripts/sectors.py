"""Sector research table: one row per S&P 500 sector, measured through its SPDR sector ETF against SPY.

Run with runpy after loading fmp.py (this script calls its fetchers); nothing executes on import:
    fmp = runpy.run_path('skills/curated/sector-allocation/scripts/fmp.py')
    sectors = runpy.run_path('skills/curated/sector-allocation/scripts/sectors.py')
    table = sectors['sector_table'](fmp)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

BENCHMARK = 'SPY'
TRADING_DAYS = 252

# FMP sector name (as in SPY's sector weightings and the sector P/E endpoints) -> SPDR sector ETF
SECTOR_ETFS = {
    'Technology': 'XLK',
    'Financial Services': 'XLF',
    'Communication Services': 'XLC',
    'Healthcare': 'XLV',
    'Consumer Cyclical': 'XLY',
    'Industrials': 'XLI',
    'Consumer Defensive': 'XLP',
    'Energy': 'XLE',
    'Utilities': 'XLU',
    'Basic Materials': 'XLB',
    'Real Estate': 'XLRE',
}


#     ================================
# --> Helper funcs
#     ================================


def _annualized_return(close: pd.Series, years: int) -> float:
    """Compound annual return over the trailing `years`; NaN if the series is shorter."""
    window = years * TRADING_DAYS

    if len(close) <= window:
        return float('nan')

    return float((close.iloc[-1] / close.iloc[-1 - window]) ** (1 / years) - 1)


def _max_drawdown(close: pd.Series) -> float:
    """Largest peak-to-trough fall, as a negative fraction."""
    return float((close / close.cummax() - 1).min())


def _beta(asset: pd.Series, factor: pd.Series) -> float:
    """OLS slope of asset on factor over their shared dates."""
    joined = pd.concat([asset, factor], axis=1, join='inner').dropna()

    if len(joined) < 24:
        return float('nan')

    return float(np.polyfit(joined.iloc[:, 1], joined.iloc[:, 0], 1)[0])


#     ================================
# --> Sector table
#     ================================


def sector_table(fmp: dict[str, Any], years: int = 10) -> pd.DataFrame:
    """Benchmark weight, trailing returns vs SPY, risk, rate sensitivity, and valuation for every sector.

    Columns: etf, spy_weight, ret_1y/3y/5y/10y, excess_3y/5y, vol, max_dd, beta_spy,
    beta_10y (monthly excess return per +1pp change in the 10-year yield), pe, pe_pctile (vs its own history).
    """
    weights = fmp['snapshot']('etf/sector-weightings', symbol=BENCHMARK).set_index('sector')['weightPercentage']

    closes = {symbol: fmp['price_history'](symbol).set_index('date')['close'] for symbol in [BENCHMARK, *SECTOR_ETFS.values()]}
    monthly = pd.DataFrame(closes).resample('ME').last().pct_change()

    yields = fmp['treasury_history'](years).set_index('date')['year10'].resample('ME').last().diff()

    rows: list[dict[str, Any]] = []

    for sector, etf in SECTOR_ETFS.items():
        close = closes[etf].dropna()
        daily = close.pct_change().dropna()
        excess = monthly[etf] - monthly[BENCHMARK]

        pe = fmp['sector_pe_history'](sector, years)['pe'].dropna()

        rows.append({
            'sector': sector,
            'etf': etf,
            'spy_weight': float(weights.get(sector, 0.0)),
            **{f'ret_{n}y': _annualized_return(close, n) for n in (1, 3, 5, 10)},
            **{f'excess_{n}y': _annualized_return(close, n) - _annualized_return(closes[BENCHMARK], n) for n in (3, 5)},
            'vol': float(daily.tail(3 * TRADING_DAYS).std() * np.sqrt(TRADING_DAYS)),
            'max_dd': _max_drawdown(close.tail(10 * TRADING_DAYS)),
            'beta_spy': _beta(monthly[etf], monthly[BENCHMARK]),
            'beta_10y': _beta(excess, yields),
            'pe': float(pe.iloc[-1]),
            'pe_pctile': float((pe < pe.iloc[-1]).mean()),
        })

    return pd.DataFrame(rows).set_index('sector').sort_values('spy_weight', ascending=False)
