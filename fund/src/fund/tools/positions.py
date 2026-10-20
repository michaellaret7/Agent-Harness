"""Account and position tools for the PMs: what the paper account holds and what it can still buy.

    GetAccount()                              equity, cash, buying power, and whether the market is open
    GetPositions(symbol)                      open positions with cost, value, and unrealized P&L
    ClosePosition(symbol, qty | percentage)   sell (or cover) all or part of a position at market
"""
from __future__ import annotations

from typing import Annotated, Any

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.models import Position
from alpaca.trading.requests import ClosePositionRequest

from agent_harness.tooling.decorator import Param, agent_tool, bind_tool
from agent_harness.tooling.result import ToolResult
from fund.tools.broker import broker_error, order_line


#     ================================
# --> Helper funcs
#     ================================


def _position_line(position: Position) -> str:
    """One position as the model sees it."""
    pl_percent = float(position.unrealized_plpc or 0) * 100

    return (
        f'- {position.symbol}: {position.side.value} {position.qty} sh @ avg {position.avg_entry_price}, '
        f'now {position.current_price}, value {position.market_value}, '
        f'unrealized P&L {position.unrealized_pl} ({pl_percent:+.2f}%), today {position.unrealized_intraday_pl}'
    )


#     ================================
# --> Tools
#     ================================


@agent_tool(name='GetAccount', safe_parallel=True)
def get_account(_client: TradingClient | None = None) -> ToolResult:
    """Show the paper account's equity, cash, and buying power, and whether the market is open."""
    assert _client is not None, 'GetAccount must be bound to a TradingClient'

    try:
        account = _client.get_account()
        clock = _client.get_clock()

    except APIError as error:
        return broker_error(error)

    market = 'open' if clock.is_open else 'closed'

    lines = [
        f'Equity {account.equity} (last close {account.last_equity}), cash {account.cash}, buying power {account.buying_power}',
        f'Long value {account.long_market_value}, short value {account.short_market_value}, day trades (5 days) {account.daytrade_count}',
        f'Market {market}; next open {clock.next_open:%Y-%m-%d %H:%M %Z}, next close {clock.next_close:%Y-%m-%d %H:%M %Z}',
    ]

    if account.trading_blocked or account.account_blocked:
        lines.append('Trading is BLOCKED on this account.')

    return ToolResult('\n'.join(lines), status='ok')


@agent_tool(name='GetPositions', safe_parallel=True)
def get_positions(
    symbol: Annotated[str | None, Param(description='One ticker; leave empty for every open position.')] = None,
    _client: TradingClient | None = None,
) -> ToolResult:
    """Show open positions with entry price, current value, and unrealized P&L."""
    assert _client is not None, 'GetPositions must be bound to a TradingClient'

    try:
        positions = [_client.get_open_position(symbol.strip().upper())] if symbol else _client.get_all_positions()

    except APIError as error:
        return broker_error(error)

    if not positions:
        return ToolResult('No open positions.', status='ok')

    return ToolResult('\n'.join(_position_line(position) for position in positions), status='ok')


@agent_tool(name='ClosePosition')
def close_position(
    symbol: Annotated[str, Param(description='Ticker of the position to close.')],
    qty: Annotated[float | None, Param(description='Shares to close. Give this or percentage; neither closes it all.')] = None,
    percentage: Annotated[float | None, Param(description='Percent of the position to close.', min_val=0, max_val=100)] = None,
    _client: TradingClient | None = None,
) -> ToolResult:
    """Close all or part of a position with a market order. Cancel its open exit orders first, or Alpaca rejects the close."""
    assert _client is not None, 'ClosePosition must be bound to a TradingClient'

    try:
        options = None

        if qty is not None or percentage is not None:
            options = ClosePositionRequest(
                qty=str(qty) if qty is not None else None,
                percentage=str(percentage) if percentage is not None else None,
            )

        order = _client.close_position(symbol.strip().upper(), close_options=options)

    except (APIError, ValueError) as error:
        return broker_error(error)

    return ToolResult(f'Close order submitted:\n{order_line(order)}', status='ok')


def position_tools(client: TradingClient) -> list[dict[str, Any]]:
    """The three account and position tools bound to `client`."""
    return [bind_tool(tool, _client=client) for tool in (get_account, get_positions, close_position)]
