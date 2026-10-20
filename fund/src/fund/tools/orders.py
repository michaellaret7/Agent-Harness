"""Order tools for the PMs: place, list, change, and cancel orders in the Alpaca paper account.

    SubmitOrder(symbol, side, order_type, qty | notional, ...)   one order; bracket / oco / oto exits attach to it
    GetOrders(status, symbols, limit)                             open, closed, or all orders, newest first
    ReplaceOrder(order_id, qty, limit_price, stop_price, ...)     change an open order in place
    CancelOrder(order_id)                                         cancel an open order

Requests are built with alpaca-py's typed requests (`alpaca.trading.requests`), so the SDK's
own validation (qty xor notional, required prices per type, bracket legs) runs before anything
is sent; Alpaca's server-side rules (TIF per type, price increments, buying power) come back as
an error result the model can read and correct.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    OrderRequest,
    ReplaceOrderRequest,
    StopLimitOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
    TrailingStopOrderRequest,
)

from agent_harness.tooling.decorator import Param, agent_tool, bind_tool
from agent_harness.tooling.result import ToolResult
from fund.tools.broker import broker_error, order_line

OrderType = Literal['market', 'limit', 'stop', 'stop_limit', 'trailing_stop']
OrderClass = Literal['simple', 'bracket', 'oco', 'oto']
TimeInForce = Literal['day', 'gtc', 'opg', 'cls', 'ioc', 'fok']

ORDER_REQUESTS: dict[str, type[OrderRequest]] = {
    'market': MarketOrderRequest,
    'limit': LimitOrderRequest,
    'stop': StopOrderRequest,
    'stop_limit': StopLimitOrderRequest,
    'trailing_stop': TrailingStopOrderRequest,
}

ORDER_RULES = """Alpaca rules (US equities):
- Give exactly one of qty (shares) or notional (dollars). Fractional qty and notional orders take time_in_force "day" only.
- limit needs limit_price; stop needs stop_price; stop_limit needs both; trailing_stop needs one of trail_percent or trail_price, "day" or "gtc".
- bracket: an entry plus take_profit_price and stop_loss_price, "day" or "gtc", whole shares, no extended hours. Exits activate once the entry fills.
- oco: exits for a position you already hold. order_type "limit", take_profit_price and stop_loss_price, same side as the exit.
- oto: an entry plus one exit (take_profit_price or stop_loss_price).
- extended_hours: limit orders only, "day" or "gtc".
- Prices at or above $1 take at most 2 decimals."""


#     ================================
# --> Helper funcs
#     ================================


def _exit_legs(take_profit_price: float | None, stop_loss_price: float | None, stop_loss_limit_price: float | None) -> dict[str, Any]:
    """The take-profit and stop-loss legs of a bracket, oco, or oto order, only those that were given."""
    legs: dict[str, Any] = {}

    if take_profit_price is not None:
        legs['take_profit'] = TakeProfitRequest(limit_price=take_profit_price)

    if stop_loss_price is not None:
        legs['stop_loss'] = StopLossRequest(stop_price=stop_loss_price, limit_price=stop_loss_limit_price)

    return legs


#     ================================
# --> Tools
#     ================================


@agent_tool(name='SubmitOrder')
def submit_order(
    symbol: Annotated[str, Param(description='Ticker, e.g. "NVDA".')],
    side: Annotated[Literal['buy', 'sell'], Param(description='buy or sell.')],
    order_type: Annotated[OrderType, Param(description='market, limit, stop, stop_limit or trailing_stop.')],
    time_in_force: Annotated[TimeInForce, Param(description='day, gtc, opg, cls, ioc or fok.')] = 'day',
    qty: Annotated[float | None, Param(description='Shares. Give this or notional, not both.')] = None,
    notional: Annotated[float | None, Param(description='Dollars to trade (market, limit, stop orders, "day" only).')] = None,
    limit_price: Annotated[float | None, Param(description='Worst fill price for limit and stop_limit orders.')] = None,
    stop_price: Annotated[float | None, Param(description='Trigger price for stop and stop_limit orders.')] = None,
    trail_percent: Annotated[float | None, Param(description='Trailing stop distance in percent.')] = None,
    trail_price: Annotated[float | None, Param(description='Trailing stop distance in dollars.')] = None,
    order_class: Annotated[OrderClass, Param(description='simple, bracket, oco or oto.')] = 'simple',
    take_profit_price: Annotated[float | None, Param(description='Limit price of the take-profit exit.')] = None,
    stop_loss_price: Annotated[float | None, Param(description='Trigger price of the stop-loss exit.')] = None,
    stop_loss_limit_price: Annotated[float | None, Param(description='Makes the stop-loss exit a stop-limit at this price.')] = None,
    extended_hours: Annotated[bool, Param(description='Allow pre-market and after-hours fills (limit orders only).')] = False,
    client_order_id: Annotated[str | None, Param(description='Your own unique id for the order, e.g. the idea it executes.')] = None,
    _client: TradingClient | None = None,
) -> ToolResult:
    """Place an order in the paper account. Returns the order as Alpaca accepted it, with its id."""
    assert _client is not None, 'SubmitOrder must be bound to a TradingClient'

    fields = {
        'symbol': symbol.strip().upper(),
        'side': side,
        'time_in_force': time_in_force,
        'qty': qty,
        'notional': notional,
        'limit_price': limit_price,
        'stop_price': stop_price,
        'trail_percent': trail_percent,
        'trail_price': trail_price,
        'order_class': order_class,
        'extended_hours': extended_hours or None,
        'client_order_id': client_order_id,
        **_exit_legs(take_profit_price, stop_loss_price, stop_loss_limit_price),
    }

    # Each request class rejects the price fields its order type does not use, so pass only the set ones
    request_fields = {name: value for name, value in fields.items() if value is not None}

    try:
        order = _client.submit_order(ORDER_REQUESTS[order_type](**request_fields))

    except (APIError, ValueError) as error:
        return broker_error(error)

    return ToolResult(f'Order submitted:\n{order_line(order)}', status='ok', data=order.model_dump(mode='json'))


submit_order.tool['description'] += f'\n\n{ORDER_RULES}'


@agent_tool(name='GetOrders', safe_parallel=True)
def get_orders(
    status: Annotated[Literal['open', 'closed', 'all'], Param(description='open, closed or all.')] = 'open',
    symbols: Annotated[list[str] | None, Param(description='Only orders for these tickers.')] = None,
    limit: Annotated[int, Param(description='Maximum orders to return.', min_val=1, max_val=500)] = 50,
    _client: TradingClient | None = None,
) -> ToolResult:
    """List orders in the paper account, newest first, with bracket legs nested under their entry."""
    assert _client is not None, 'GetOrders must be bound to a TradingClient'

    request = GetOrdersRequest(
        status=QueryOrderStatus(status),
        symbols=[symbol.strip().upper() for symbol in symbols] if symbols else None,
        limit=limit,
        nested=True,
    )

    try:
        orders = _client.get_orders(filter=request)

    except (APIError, ValueError) as error:
        return broker_error(error)

    if not orders:
        return ToolResult(f'No {status} orders.', status='ok')

    return ToolResult('\n'.join(order_line(order) for order in orders), status='ok')


@agent_tool(name='ReplaceOrder')
def replace_order(
    order_id: Annotated[str, Param(description='The order id shown in brackets.')],
    qty: Annotated[int | None, Param(description='New whole-share quantity.', min_val=1)] = None,
    limit_price: Annotated[float | None, Param(description='New limit price.')] = None,
    stop_price: Annotated[float | None, Param(description='New stop price.')] = None,
    trail: Annotated[float | None, Param(description='New trail amount, in the trailing stop\'s own unit.')] = None,
    time_in_force: Annotated[TimeInForce | None, Param(description='New time in force.')] = None,
    _client: TradingClient | None = None,
) -> ToolResult:
    """Change an open order in place.

    Only an order already working at the exchange (status new or partially_filled) can be replaced; one still
    "accepted" while the market is closed cannot. Notional and oto orders cannot be replaced either. In those
    cases cancel and resubmit.
    """
    assert _client is not None, 'ReplaceOrder must be bound to a TradingClient'

    try:
        request = ReplaceOrderRequest(
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            trail=trail,
            time_in_force=time_in_force,
        )

        order = _client.replace_order_by_id(order_id, request)

    except (APIError, ValueError) as error:
        return broker_error(error)

    return ToolResult(
        f'Order replaced (the new order has a new id):\n{order_line(order)}',
        status='ok',
        data=order.model_dump(mode='json'),
    )


@agent_tool(name='CancelOrder')
def cancel_order(
    order_id: Annotated[str, Param(description='The order id shown in brackets.')],
    _client: TradingClient | None = None,
) -> ToolResult:
    """Cancel an open order. Cancelling one leg of a bracket cancels the whole group."""
    assert _client is not None, 'CancelOrder must be bound to a TradingClient'

    try:
        _client.cancel_order_by_id(order_id)

    except (APIError, ValueError) as error:
        return broker_error(error)

    return ToolResult(f'Cancel requested for [{order_id}]. Check GetOrders for its final status.', status='ok')


def order_tools(client: TradingClient) -> list[dict[str, Any]]:
    """The four order tools bound to `client`."""
    return [bind_tool(tool, _client=client) for tool in (submit_order, get_orders, replace_order, cancel_order)]
