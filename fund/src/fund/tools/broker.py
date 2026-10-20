"""The fund's Alpaca paper-trading client, and how its orders and errors read to the model.

    paper_client()  ──► TradingClient(paper=True)   one per process, shared by every trading tool
    order_line()    ──► one order (plus its bracket legs) as a single readable line
    broker_error()  ──► an Alpaca rejection or a request the SDK refused, as an error ToolResult

The client is always the paper account: nothing in `fund` can build a live one.
"""
from __future__ import annotations

import os

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.models import Order

from agent_harness.tooling.result import ToolResult

# Host credentials for the paper account (`.env`); the sandbox kernel never sees them
ALPACA_CREDENTIALS = ('APCA_API_KEY_ID', 'APCA_API_SECRET_KEY')


#     ================================
# --> Helper funcs
#     ================================


def _amount(order: Order) -> str:
    """The order's size: shares, or dollars for a notional order."""
    if order.notional is not None:
        return f'${order.notional}'

    return f'{order.qty} sh'


def _prices(order: Order) -> str:
    """The order's limit, stop and trail settings, only those it has."""
    parts = [
        f'limit {order.limit_price}' if order.limit_price is not None else '',
        f'stop {order.stop_price}' if order.stop_price is not None else '',
        f'trail {order.trail_percent}%' if order.trail_percent is not None else '',
        f'trail ${order.trail_price}' if order.trail_price is not None else '',
    ]

    return ' '.join(part for part in parts if part)


#     ================================
# --> Client and rendering
#     ================================


def paper_client() -> TradingClient:
    """The paper-trading client. Fails fast with a KeyError if a credential is missing: load `.env` first."""
    key_id, secret_key = (os.environ[name] for name in ALPACA_CREDENTIALS)

    return TradingClient(api_key=key_id, secret_key=secret_key, paper=True)


def order_line(order: Order, indent: str = '') -> str:
    """One order as the model sees it; the id lets it replace or cancel the order later."""
    order_type = order.type.value if order.type is not None else 'unknown'
    side = order.side.value if order.side is not None else ''

    head = (
        f'{indent}- [{order.id}] {side} {_amount(order)} {order.symbol} {order_type} '
        f'({order.order_class.value}, {order.time_in_force.value}) {_prices(order)}'
    )

    line = f'{head.rstrip()} — {order.status.value}'

    if order.filled_qty and float(order.filled_qty) > 0:
        line += f', filled {order.filled_qty} @ {order.filled_avg_price}'

    if order.client_order_id:
        line += f', client id {order.client_order_id}'

    legs = [order_line(leg, indent + '    ') for leg in order.legs or []]

    return '\n'.join([line, *legs])


def broker_error(error: Exception) -> ToolResult:
    """An Alpaca rejection (APIError) or an order the SDK refused to build (ValueError) as an error result."""
    if isinstance(error, APIError):
        try:
            return ToolResult(f'error: Alpaca rejected the request: {error.message}', status='error')

        except (ValueError, KeyError, TypeError):
            return ToolResult(f'error: Alpaca rejected the request: {error}', status='error')

    return ToolResult(f'error: invalid request: {error}', status='error')
