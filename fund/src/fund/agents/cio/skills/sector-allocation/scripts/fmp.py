"""FMP stable-API fetchers for the macro dive and sector research, cached to Parquet in `data/`.

Run with runpy; nothing executes on import:
    fmp = runpy.run_path('skills/curated/sector-allocation/scripts/fmp.py')
    cpi = fmp['indicator_history']('CPI', years=10)
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

BASE_URL = 'https://financialmodelingprep.com/stable/'
CACHE_DIR = Path('data')

# economic-indicators and treasury-rates return at most ~90 days per call, so history is fetched in windows
WINDOW_DAYS = 89
MAX_RETRIES = 5
PARALLEL_REQUESTS = 4  # stays under the per-minute rate limit; 429s back off in fmp_get


#     ================================
# --> Helper funcs
#     ================================


def _cached(name: str, fetch: Any) -> pd.DataFrame:
    """Return `data/<name>.parquet` if saved today, else fetch, save, and return it."""
    path = CACHE_DIR / f'{name}.parquet'

    if path.exists() and date.fromtimestamp(path.stat().st_mtime) == date.today():
        return pd.read_parquet(path)

    frame = fetch()

    CACHE_DIR.mkdir(exist_ok=True)
    frame.to_parquet(path)

    return frame


def _windowed(path: str, years: int, **params: Any) -> pd.DataFrame:
    """Fetch a date-capped endpoint in WINDOW_DAYS windows covering the last `years`, oldest first, deduplicated."""
    end = date.today()
    start = end - timedelta(days=365 * years)
    windows: list[tuple[date, date]] = []

    while end > start:
        window_start = max(start, end - timedelta(days=WINDOW_DAYS))
        windows.append((window_start, end))
        end = window_start - timedelta(days=1)

    def fetch(window: tuple[date, date]) -> list[dict]:
        return fmp_get(path, **params, **{'from': window[0].isoformat(), 'to': window[1].isoformat()})

    with ThreadPoolExecutor(PARALLEL_REQUESTS) as pool:
        rows = [row for chunk in pool.map(fetch, windows) for row in chunk]

    if not rows:
        raise ValueError(f'{path} {params}: no rows in the last {years} years')

    frame = pd.DataFrame(rows).drop_duplicates('date')
    frame['date'] = pd.to_datetime(frame['date'])

    return frame.sort_values('date').reset_index(drop=True)


#     ================================
# --> Fetchers
#     ================================


def fmp_get(path: str, **params: Any) -> list[dict]:
    """GET one FMP stable endpoint as a list of rows; retries HTTP 429 with backoff, raises on any other error."""
    for attempt in range(MAX_RETRIES):
        response = httpx.get(BASE_URL + path, params={**params, 'apikey': os.environ['FMP_API_KEY']}, timeout=60)

        if response.status_code == 429:
            time.sleep(2 ** attempt * 5)
            continue

        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict) and 'Error Message' in data:
            raise ValueError(f'FMP {path}: {data["Error Message"]}')

        if isinstance(data, str):
            raise ValueError(f'FMP {path}: {data}')

        return data if isinstance(data, list) else [data]

    raise RuntimeError(f'FMP {path}: still rate-limited after {MAX_RETRIES} retries')


def indicator_history(name: str, years: int = 10) -> pd.DataFrame:
    """One economic indicator (see references/macro-indicators.md) as date, value; oldest first."""
    return _cached(f'indicator_{name}_{years}y', lambda: _windowed('economic-indicators', years, name=name)[['date', 'value']])


def treasury_history(years: int = 10) -> pd.DataFrame:
    """Daily US Treasury curve (month1 … year30, percent); oldest first."""
    return _cached(f'treasury_{years}y', lambda: _windowed('treasury-rates', years))


def price_history(symbol: str, start: str = '2005-01-01') -> pd.DataFrame:
    """Daily dividend-adjusted closes for one symbol as date, close; oldest first."""
    def fetch() -> pd.DataFrame:
        frame = pd.DataFrame(fmp_get('historical-price-eod/dividend-adjusted', symbol=symbol, **{'from': start}))
        frame = frame.rename(columns={'adjClose': 'close'})[['date', 'close']]
        frame['date'] = pd.to_datetime(frame['date'])

        return frame.sort_values('date').reset_index(drop=True)

    return _cached(f'prices_{symbol.replace("^", "")}_{start}', fetch)


def sector_pe_history(sector: str, years: int = 10, exchange: str = 'NYSE') -> pd.DataFrame:
    """Daily trailing P/E for one FMP sector on one exchange as date, pe; oldest first."""
    def fetch() -> pd.DataFrame:
        start = (date.today() - timedelta(days=365 * years)).isoformat()
        frame = pd.DataFrame(fmp_get('historical-sector-pe', sector=sector, exchange=exchange, **{'from': start, 'to': date.today().isoformat()}))
        frame['date'] = pd.to_datetime(frame['date'])

        return frame[['date', 'pe']].sort_values('date').reset_index(drop=True)

    return _cached(f'sector_pe_{sector.replace(" ", "_")}_{exchange}_{years}y', fetch)


def snapshot(path: str, **params: Any) -> pd.DataFrame:
    """Any single-call endpoint (quotes, sector snapshots, SPY weights, calendar) as a DataFrame, uncached."""
    return pd.DataFrame(fmp_get(path, **params))
