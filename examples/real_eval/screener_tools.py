"""Alpaca market-data tools for the stock screener agent.

Each tool pulls one kind of data from Alpaca's REST API and saves it as a CSV
in the agent's sandbox workspace, returning only the row count, columns and a
short preview. The agent then loads the CSV with pandas in ExecuteCode and
builds the screen there, so a 5,000-symbol pull never touches its context.

    ListAssets ─────┐
    GetDailyBars ───┤
    GetSnapshots ───┼──► <workspace>/<save_as>.csv ──► ExecuteCode (pandas screen)
    GetMarketMovers*┤
    GetMostActives* ┤
    GetNews* ───────┘                               * deferred: load with LoadTool first

`screener_tools(workspace)` binds every tool to one workspace; the agent
factory registers the list next to an ExecuteCode tool on the same folder.
Feeds: daily bars use SIP (consolidated, all venues) ending 16 minutes ago,
which the free plan allows; snapshots are IEX-only, so their volumes cover a
few percent of the market and must not be used for volume filters.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import httpx

from agent_harness.tooling.decorator import Param, agent_tool, bind_tool
from agent_harness.tooling.result import ToolResult

DATA_URL = 'https://data.alpaca.markets'
TRADING_URL = 'https://paper-api.alpaca.markets'
SYMBOL_BATCH = 200                              # symbols per request, keeps the query string short
PREVIEW_ROWS = 3


#     ================================
# --> Helper funcs
#     ================================


def _get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET an Alpaca endpoint with the account's keys and return the JSON body. Raises on HTTP errors."""
    headers = {
        'APCA-API-KEY-ID': os.environ['APCA_API_KEY_ID'],
        'APCA-API-SECRET-KEY': os.environ['APCA_API_SECRET_KEY'],
    }

    response = httpx.get(url, params=params, headers=headers, timeout=60)
    response.raise_for_status()

    return response.json()


def _get_paged(url: str, params: dict[str, Any], key: str) -> list[Any]:
    """Follow `next_page_token` until exhausted; collect each page's `key` value."""
    pages: list[Any] = []
    token: str | None = None

    while True:
        body = _get(url, {**params, 'page_token': token} if token else params)

        pages.append(body[key])

        token = body.get('next_page_token')

        if not token:
            return pages


def _batches(symbols: list[str]) -> list[list[str]]:
    """Upper-case, de-duplicate and chunk symbols into request-sized batches."""
    unique = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))

    return [unique[i:i + SYMBOL_BATCH] for i in range(0, len(unique), SYMBOL_BATCH)]


def _save(workspace: Path | None, save_as: str, rows: list[dict[str, Any]]) -> ToolResult:
    """Write rows to `<workspace>/<save_as>` and report count, columns and a preview."""
    if workspace is None:
        raise RuntimeError('screener tool used without a bound workspace; build tools with screener_tools()')

    if Path(save_as).name != save_as or not save_as.endswith('.csv'):
        return ToolResult(f'error: save_as must be a bare file name ending in .csv, got {save_as!r}', status='error')

    if not rows:
        return ToolResult(f'no rows returned; nothing saved to {save_as}', status='ok')

    columns = list(rows[0])

    with (workspace / save_as).open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    preview = '\n'.join(str(row) for row in rows[:PREVIEW_ROWS])

    return ToolResult(
        f'saved {len(rows)} rows to {save_as} in the working directory\ncolumns: {", ".join(columns)}\nfirst rows:\n{preview}',
        status='ok',
    )


def _fetch(workspace: Path | None, save_as: str, pull: Any) -> ToolResult:
    """Run `pull()` and save its rows; an HTTP failure becomes an error result the model can read."""
    try:
        rows = pull()

    except httpx.HTTPStatusError as e:
        return ToolResult(f'error: Alpaca returned {e.response.status_code}: {e.response.text[:300]}', status='error')

    return _save(workspace, save_as, rows)


#     ================================
# --> Core tools
#     ================================


@agent_tool(name='ListAssets', safe_parallel=True)
def list_assets(
    save_as: Annotated[str, Param(description='CSV file name to write in the working directory, e.g. "assets.csv".')],
    exchanges: Annotated[list[str] | None, Param(description='Keep only these exchanges, e.g. ["NYSE", "NASDAQ"]. Omit for all, including OTC.')] = None,
    _workspace: Path | None = None,
) -> ToolResult:
    """
    List active, tradable US equities and ETFs on Alpaca: the universe a screen
    starts from. Saves symbol, name, exchange, shortable, easy_to_borrow,
    fractionable and marginable per asset. Has no prices or fundamentals.
    """
    keep = {e.upper() for e in exchanges or []}

    def pull() -> list[dict[str, Any]]:
        assets = _get(f'{TRADING_URL}/v2/assets', {'status': 'active', 'asset_class': 'us_equity'})

        return [
            {
                'symbol': a['symbol'], 'name': a['name'], 'exchange': a['exchange'],
                'shortable': a['shortable'], 'easy_to_borrow': a['easy_to_borrow'],
                'fractionable': a['fractionable'], 'marginable': a['marginable'],
            }
            for a in assets
            if a['tradable'] and (not keep or a['exchange'] in keep)
        ]

    return _fetch(_workspace, save_as, pull)


@agent_tool(name='GetDailyBars', safe_parallel=True)
def get_daily_bars(
    symbols: Annotated[list[str], Param(description='Ticker symbols. Thousands are fine; they are batched.')],
    days: Annotated[int, Param(description='Calendar days of history ending today. 300 covers a 200-day average.', min_val=1, max_val=1500)],
    save_as: Annotated[str, Param(description='CSV file name to write in the working directory, e.g. "bars.csv".')],
    _workspace: Path | None = None,
) -> ToolResult:
    """
    Daily OHLCV bars, split- and dividend-adjusted, from the consolidated SIP
    feed (all US venues, so volumes are full-market). History ends 16 minutes
    ago, so during market hours today's bar is partial. Saves one row per
    symbol per day: symbol, date, open, high, low, close, volume, trade_count, vwap.
    """
    end = datetime.now(timezone.utc) - timedelta(minutes=16)
    start = end - timedelta(days=days)

    def pull() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for batch in _batches(symbols):
            params = {
                'symbols': ','.join(batch), 'timeframe': '1Day', 'feed': 'sip', 'adjustment': 'all',
                'start': start.isoformat(), 'end': end.isoformat(), 'limit': 10000,
            }

            for page in _get_paged(f'{DATA_URL}/v2/stocks/bars', params, 'bars'):
                for symbol, bars in page.items():
                    rows.extend(
                        {'symbol': symbol, 'date': b['t'][:10], 'open': b['o'], 'high': b['h'], 'low': b['l'],
                         'close': b['c'], 'volume': b['v'], 'trade_count': b['n'], 'vwap': b['vw']}
                        for b in bars
                    )

        return rows

    return _fetch(_workspace, save_as, pull)


@agent_tool(name='GetSnapshots', safe_parallel=True)
def get_snapshots(
    symbols: Annotated[list[str], Param(description='Ticker symbols. Thousands are fine; they are batched.')],
    save_as: Annotated[str, Param(description='CSV file name to write in the working directory, e.g. "snapshots.csv".')],
    _workspace: Path | None = None,
) -> ToolResult:
    """
    Latest price picture per symbol from the IEX feed: last trade price and
    time, bid and ask, today's open/high/low/close, and the previous close.
    IEX is one venue, so `day_volume` here is a few percent of real volume:
    use GetDailyBars for any volume figure.
    """
    def pull() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for batch in _batches(symbols):
            snapshots = _get(f'{DATA_URL}/v2/stocks/snapshots', {'symbols': ','.join(batch), 'feed': 'iex'})

            for symbol, s in snapshots.items():
                trade, quote = s.get('latestTrade') or {}, s.get('latestQuote') or {}
                day, prev = s.get('dailyBar') or {}, s.get('prevDailyBar') or {}

                rows.append({
                    'symbol': symbol, 'last_price': trade.get('p'), 'last_trade_time': trade.get('t'),
                    'bid': quote.get('bp'), 'ask': quote.get('ap'),
                    'day_open': day.get('o'), 'day_high': day.get('h'), 'day_low': day.get('l'), 'day_close': day.get('c'),
                    'day_volume': day.get('v'), 'prev_close': prev.get('c'),
                })

        return rows

    return _fetch(_workspace, save_as, pull)


#     ================================
# --> Deferred tools
#     ================================


@agent_tool(name='GetMarketMovers', deferred=True, safe_parallel=True)
def get_market_movers(
    top: Annotated[int, Param(description='How many gainers and how many losers to return.', min_val=1, max_val=50)],
    save_as: Annotated[str, Param(description='CSV file name to write in the working directory, e.g. "movers.csv".')],
    _workspace: Path | None = None,
) -> ToolResult:
    """
    Today's biggest US stock gainers and losers by percent change versus the
    previous close. Saves side (gainer | loser), symbol, price, change and
    percent_change. Includes penny stocks, warrants and rights; filter them yourself.
    """
    def pull() -> list[dict[str, Any]]:
        body = _get(f'{DATA_URL}/v1beta1/screener/stocks/movers', {'top': top})

        return [{'side': side[:-1], **m} for side in ('gainers', 'losers') for m in body[side]]

    return _fetch(_workspace, save_as, pull)


@agent_tool(name='GetMostActives', deferred=True, safe_parallel=True)
def get_most_actives(
    top: Annotated[int, Param(description='How many symbols to return.', min_val=1, max_val=100)],
    save_as: Annotated[str, Param(description='CSV file name to write in the working directory, e.g. "actives.csv".')],
    by: Annotated[str, Param(description='Rank by share volume or by number of trades.', enum=['volume', 'trades'])] = 'volume',
    _workspace: Path | None = None,
) -> ToolResult:
    """
    Today's most active US stocks, ranked by share volume or trade count.
    Saves symbol, volume and trade_count. A ready-made liquid universe.
    """
    def pull() -> list[dict[str, Any]]:
        return _get(f'{DATA_URL}/v1beta1/screener/stocks/most-actives', {'top': top, 'by': by})['most_actives']

    return _fetch(_workspace, save_as, pull)


@agent_tool(name='GetNews', deferred=True, safe_parallel=True)
def get_news(
    symbols: Annotated[list[str], Param(description='Ticker symbols to fetch headlines for.')],
    days: Annotated[int, Param(description='Calendar days of news ending now.', min_val=1, max_val=30)],
    save_as: Annotated[str, Param(description='CSV file name to write in the working directory, e.g. "news.csv".')],
    _workspace: Path | None = None,
) -> ToolResult:
    """
    Recent news headlines for the given symbols (Benzinga via Alpaca). Saves
    created_at, symbols, headline, summary, source and url, newest first.
    Use it to explain why a screened stock moved, not to screen.
    """
    start = datetime.now(timezone.utc) - timedelta(days=days)

    def pull() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for batch in _batches(symbols):
            # One page (the newest 50) per batch: enough to explain a move, and a busy ticker can't page forever.
            params = {'symbols': ','.join(batch), 'start': start.isoformat(), 'limit': 50, 'sort': 'desc'}

            rows.extend(
                {'created_at': n['created_at'], 'symbols': ' '.join(n['symbols']), 'headline': n['headline'],
                 'summary': n['summary'], 'source': n['source'], 'url': n['url']}
                for n in _get(f'{DATA_URL}/v1beta1/news', params)['news']
            )

        return rows

    return _fetch(_workspace, save_as, pull)


#     ================================
# --> Factory
#     ================================


def screener_tools(workspace: Path) -> list[dict[str, Any]]:
    """Every screener tool bound to one workspace, the same folder ExecuteCode runs in."""
    tools = (list_assets, get_daily_bars, get_snapshots, get_market_movers, get_most_actives, get_news)

    return [bind_tool(fn, _workspace=workspace) for fn in tools]
