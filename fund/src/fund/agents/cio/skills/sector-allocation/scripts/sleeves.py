"""Non-US-equity sleeve research: fixed-income buckets and international regions, measured through proxy ETFs vs SPY.

Run with runpy after loading fmp.py (this script calls its fetchers); nothing executes on import:
    fmp = runpy.run_path('skills/curated/sector-allocation/scripts/fmp.py')
    sleeves = runpy.run_path('skills/curated/sector-allocation/scripts/sleeves.py')
    bonds, international = sleeves['bond_table'](fmp), sleeves['international_table'](fmp)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

BENCHMARK = 'SPY'
TRADING_DAYS = 252

# Fixed-income bucket -> (proxy ETF, Treasury curve column closest to its duration)
BOND_BUCKETS = {
    'Short Treasuries': ('SHY', 'year2'),
    'Intermediate Treasuries': ('IEF', 'year7'),
    'Long Treasuries': ('TLT', 'year20'),
    'TIPS': ('TIP', 'year7'),
    'Investment-grade credit': ('LQD', 'year10'),
    'High yield': ('HYG', 'year5'),
}

# International region -> proxy ETF
REGIONS = {
    'Developed ex-US': 'EFA',
    'Emerging markets': 'EEM',
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


def _risk_row(close: pd.Series, spy: pd.Series) -> dict[str, float]:
    """Trailing returns, excess vs SPY, volatility, drawdown, and monthly correlation to SPY."""
    monthly = pd.concat([close, spy], axis=1, join='inner').resample('ME').last().pct_change().dropna()

    return {
        **{f'ret_{n}y': _annualized_return(close, n) for n in (1, 3, 5, 10)},
        'excess_5y': _annualized_return(close, 5) - _annualized_return(spy, 5),
        'vol': float(close.pct_change().tail(3 * TRADING_DAYS).std() * np.sqrt(TRADING_DAYS)),
        'max_dd': float((close.tail(10 * TRADING_DAYS) / close.tail(10 * TRADING_DAYS).cummax() - 1).min()),
        'corr_spy': float(monthly.iloc[:, 0].corr(monthly.iloc[:, 1])),
    }


#     ================================
# --> Sleeve tables
#     ================================


def bond_table(fmp: dict[str, Any], years: int = 10) -> pd.DataFrame:
    """One row per fixed-income bucket: proxy ETF, matching Treasury yield now and its 10-year percentile, and risk vs SPY.

    `corr_spy` is the diversification test: below zero, the bucket hedges equities; above, it does not.
    """
    spy = fmp['price_history'](BENCHMARK).set_index('date')['close']
    curve = fmp['treasury_history'](years).set_index('date')
    rows: list[dict[str, Any]] = []

    for bucket, (etf, tenor) in BOND_BUCKETS.items():
        close = fmp['price_history'](etf).set_index('date')['close'].dropna()
        yields = curve[tenor].dropna()

        rows.append({
            'bucket': bucket,
            'etf': etf,
            'tenor': tenor,
            'yield_now': float(yields.iloc[-1]),
            'yield_pctile': float((yields < yields.iloc[-1]).mean()),
            **_risk_row(close, spy),
        })

    return pd.DataFrame(rows).set_index('bucket')


def international_table(fmp: dict[str, Any]) -> pd.DataFrame:
    """One row per international region: proxy ETF and its returns and risk vs SPY, plus the dollar's 1-year change.

    A falling dollar (`usd_1y` < 0) lifts non-US returns for a US investor; a rising one hurts them.
    """
    spy = fmp['price_history'](BENCHMARK).set_index('date')['close']
    dollar = fmp['price_history']('DX-Y.NYB').set_index('date')['close']
    usd_1y = _annualized_return(dollar, 1)
    rows: list[dict[str, Any]] = []

    for region, etf in REGIONS.items():
        close = fmp['price_history'](etf).set_index('date')['close'].dropna()

        rows.append({'region': region, 'etf': etf, **_risk_row(close, spy), 'usd_1y': usd_1y})

    return pd.DataFrame(rows).set_index('region')
