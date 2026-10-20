"""Trade-record contracts. Run: uv run --all-packages pytest tests/test_fund_trades.py

A scripted model calls trading tools through the real loop, so the tests grade the real hook
wiring, filtering, and the full order carried on ToolResult.data. Most tools are stand-ins
with the real names; one test runs the real SubmitOrder against a broker returning a real
Alpaca `Order` parsed from the API's JSON.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from alpaca.trading.models import Order

from agent_harness.tooling.decorator import agent_tool, bind_tool
from agent_harness.tooling.result import ToolResult
from contract_support import scripted_agent, tool_call, tool_delta
from fund.tools.orders import submit_order
from fund.trades import Trade, get_trades, record_trades

ROLE = 'PM (Test Pod)'

# An order as Alpaca's API returns it (a bracket entry with its two exit legs)
ORDER_JSON: dict[str, Any] = {
    'id': '61e69015-8549-4bfd-b9c3-01e75843f47d',
    'client_order_id': 'nvda-ai-capex',
    'created_at': '2026-10-21T13:31:02.123Z',
    'updated_at': '2026-10-21T13:31:02.456Z',
    'submitted_at': '2026-10-21T13:31:02.120Z',
    'filled_at': None, 'expired_at': None, 'canceled_at': None, 'failed_at': None,
    'replaced_at': None, 'replaced_by': None, 'replaces': None,
    'asset_id': 'b0b6dd9d-8b9b-48a9-ba46-b9d54906e415',
    'symbol': 'NVDA', 'asset_class': 'us_equity',
    'notional': None, 'qty': '10', 'filled_qty': '0', 'filled_avg_price': None,
    'order_class': 'bracket', 'order_type': 'market', 'type': 'market', 'side': 'buy',
    'time_in_force': 'gtc', 'limit_price': None, 'stop_price': None,
    'status': 'accepted', 'extended_hours': False,
    'trail_percent': None, 'trail_price': None, 'hwm': None,
    'legs': [
        {
            'id': 'a1d9c1f2-5d0c-4a3e-9f61-1b2c3d4e5f60', 'client_order_id': 'nvda-tp',
            'created_at': '2026-10-21T13:31:02.123Z', 'updated_at': '2026-10-21T13:31:02.456Z',
            'submitted_at': '2026-10-21T13:31:02.120Z', 'asset_id': 'b0b6dd9d-8b9b-48a9-ba46-b9d54906e415',
            'symbol': 'NVDA', 'asset_class': 'us_equity', 'qty': '10', 'filled_qty': '0',
            'order_class': 'bracket', 'order_type': 'limit', 'type': 'limit', 'side': 'sell',
            'time_in_force': 'gtc', 'limit_price': '160', 'status': 'held', 'extended_hours': False,
        },
        {
            'id': 'b2e0d2a3-6e1d-4b4f-8a72-2c3d4e5f6071', 'client_order_id': 'nvda-sl',
            'created_at': '2026-10-21T13:31:02.123Z', 'updated_at': '2026-10-21T13:31:02.456Z',
            'submitted_at': '2026-10-21T13:31:02.120Z', 'asset_id': 'b0b6dd9d-8b9b-48a9-ba46-b9d54906e415',
            'symbol': 'NVDA', 'asset_class': 'us_equity', 'qty': '10', 'filled_qty': '0',
            'order_class': 'bracket', 'order_type': 'stop', 'type': 'stop', 'side': 'sell',
            'time_in_force': 'gtc', 'stop_price': '120', 'status': 'held', 'extended_hours': False,
        },
    ],
}


#     ================================
# --> Helper funcs
#     ================================


def stand_in_order(order_id: str, symbol: str, side: str) -> dict[str, Any]:
    """A minimal order record, enough to identify which call produced it."""
    return {'id': order_id, 'symbol': symbol, 'side': side, 'status': 'accepted'}


def broker_tools() -> list:
    """Stand-ins for the trading tools: the broker rejects a non-positive quantity."""

    @agent_tool(name='SubmitOrder')
    def submit(symbol: str, side: str, qty: float) -> ToolResult:
        """Place an order."""
        if qty <= 0:
            return ToolResult('error: Alpaca rejected the request: qty must be > 0', status='error')

        order = stand_in_order(f'order-{symbol}', symbol, side)

        return ToolResult(f'Order submitted: [{order["id"]}]', status='ok', data=order)

    @agent_tool(name='ReplaceOrder')
    def replace(order_id: str, stop_price: float) -> ToolResult:
        """Change an open order."""
        order = stand_in_order(f'{order_id}-v2', 'NVDA', 'sell') | {'stop_price': str(stop_price)}

        return ToolResult(f'Order replaced: [{order["id"]}]', status='ok', data=order)

    @agent_tool(name='ClosePosition')
    def close(symbol: str) -> ToolResult:
        """Close a position."""
        order = stand_in_order(f'close-{symbol}', symbol, 'sell')

        return ToolResult(f'Close order submitted: [{order["id"]}]', status='ok', data=order)

    @agent_tool(name='GetOrders')
    def get_orders() -> ToolResult:
        """List orders."""
        return ToolResult('No open orders.', status='ok')

    return [submit, replace, close, get_orders]


class RecordingBroker:
    """The one TradingClient method SubmitOrder uses: keeps the request, returns Alpaca's order."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def submit_order(self, order_data: Any) -> Order:
        self.requests.append(order_data)

        return Order(**ORDER_JSON)


#     ================================
# --> Contract tests
#     ================================


def test_accepted_trades_are_recorded_in_order() -> None:
    """Accepted SubmitOrder, ReplaceOrder and ClosePosition calls are recorded; rejections and reads are not."""
    member_id = uuid.uuid4()
    trades: list[Trade] = []

    calls = [
        tool_call('SubmitOrder', {'symbol': 'NVDA', 'side': 'buy', 'qty': 10}, 'c1'),
        tool_call('SubmitOrder', {'symbol': 'AMD', 'side': 'buy', 'qty': 0}, 'c2'),
        tool_call('GetOrders', {}, 'c3'),
        tool_call('ReplaceOrder', {'order_id': 'order-NVDA', 'stop_price': 120.5}, 'c4'),
        tool_call('ClosePosition', {'symbol': 'NVDA'}, 'c5'),
    ]

    with scripted_agent([[tool_delta(*calls)], [{'content': 'Done'}]], tools=broker_tools()) as (agent, api, sink):
        record_trades(agent, trades, member_id, ROLE)

        assert agent.run('Trade.', sink=sink) == 'Done'

    assert [trade.tool for trade in trades] == ['SubmitOrder', 'ReplaceOrder', 'ClosePosition']
    assert [trade.order['id'] for trade in trades] == ['order-NVDA', 'order-NVDA-v2', 'close-NVDA']
    assert all(trade.member_id == member_id and trade.role == ROLE for trade in trades)

    assert trades[0].args == {'symbol': 'NVDA', 'side': 'buy', 'qty': 10}
    assert trades[1].order['stop_price'] == '120.5'

    assert trades[0].at <= trades[1].at <= trades[2].at

    # The order is for hooks only: the model sees the reply text, never the data
    tool_messages = [m for m in agent.messages if m.get('role') == 'tool']
    assert tool_messages[0]['content'] == 'Order submitted: [order-NVDA]'


def test_real_submit_order_records_the_full_alpaca_order() -> None:
    """The real SubmitOrder puts Alpaca's whole order, legs included, in the trade as JSON."""
    broker = RecordingBroker()
    trades: list[Trade] = []

    args = {
        'symbol': 'nvda', 'side': 'buy', 'order_type': 'market', 'time_in_force': 'gtc', 'qty': 10,
        'order_class': 'bracket', 'take_profit_price': 160, 'stop_loss_price': 120,
        'client_order_id': 'nvda-ai-capex',
    }

    tools = [
        bind_tool(submit_order, _client=broker),
        *[tool for tool in broker_tools() if tool.tool['name'] != 'SubmitOrder'],
    ]

    responses = [[tool_delta(tool_call('SubmitOrder', args, 'c1'))], [{'content': 'Bought'}]]

    with scripted_agent(responses, tools=tools) as (agent, api, sink):
        record_trades(agent, trades, uuid.uuid4(), ROLE)

        assert agent.run('Buy NVDA.', sink=sink) == 'Bought'

    assert len(broker.requests) == 1
    assert len(trades) == 1

    order = trades[0].order

    assert order['id'] == ORDER_JSON['id']
    assert order['client_order_id'] == 'nvda-ai-capex'
    assert (order['symbol'], order['side'], order['qty'], order['status']) == ('NVDA', 'buy', '10', 'accepted')
    assert order['order_class'] == 'bracket'
    assert order['submitted_at'].startswith('2026-10-21T13:31:02')

    legs = {leg['type']: leg for leg in order['legs']}

    assert legs['limit']['limit_price'] == '160'
    assert legs['stop']['stop_price'] == '120'


def test_trades_persist_across_runs() -> None:
    """The record grows across runs on the same agent; it is never reset by a new turn."""
    trades: list[Trade] = []

    first = tool_call('SubmitOrder', {'symbol': 'NVDA', 'side': 'buy', 'qty': 5}, 'c1')
    second = tool_call('SubmitOrder', {'symbol': 'AVGO', 'side': 'buy', 'qty': 3}, 'c2')

    responses = [[tool_delta(first)], [{'content': 'Day 1'}], [tool_delta(second)], [{'content': 'Day 2'}]]

    with scripted_agent(responses, tools=broker_tools()) as (agent, api, sink):
        record_trades(agent, trades, uuid.uuid4(), ROLE)

        agent.run('Pre-market, day 1.', sink=sink)
        agent.run('Pre-market, day 2.', sink=sink)

    assert [trade.order['symbol'] for trade in trades] == ['NVDA', 'AVGO']


def test_agent_without_trading_tools_is_rejected() -> None:
    """Recording trades on an agent that lacks the trading tools fails fast."""
    with scripted_agent([], tools=[]) as (agent, api, sink):
        with pytest.raises(Exception, match='SubmitOrder|ReplaceOrder|ClosePosition'):
            record_trades(agent, [], uuid.uuid4(), ROLE)


def test_get_trades_reads_the_record_newest_first() -> None:
    """GetTrades lists every member's trades newest first, filters by ticker, and honours the limit."""
    pm_id, other_pm_id = uuid.uuid4(), uuid.uuid4()
    record: dict[uuid.UUID, list[Trade]] = {pm_id: [], other_pm_id: []}

    submit = tool_call('SubmitOrder', {'symbol': 'NVDA', 'side': 'buy', 'qty': 10}, 'c1')
    close = tool_call('ClosePosition', {'symbol': 'NVDA'}, 'c2')
    other = tool_call('SubmitOrder', {'symbol': 'AMD', 'side': 'buy', 'qty': 5}, 'c3')

    with scripted_agent([[tool_delta(submit)], [{'content': 'a'}], [tool_delta(close)], [{'content': 'b'}]],
                        tools=broker_tools()) as (agent, api, sink):
        record_trades(agent, record[pm_id], pm_id, 'PM (Tech)')
        agent.run('Buy.', sink=sink)
        agent.run('Exit.', sink=sink)

    with scripted_agent([[tool_delta(other)], [{'content': 'c'}]], tools=broker_tools()) as (agent, api, sink):
        record_trades(agent, record[other_pm_id], other_pm_id, 'PM (Other)')
        agent.run('Buy.', sink=sink)

    read = bind_tool(get_trades, _trades=record)['function']

    every = read().payload.splitlines()

    assert len(every) == 3
    assert 'PM (Other) · SubmitOrder' in every[0] and '[order-AMD]' in every[0]
    assert 'PM (Tech) · ClosePosition' in every[1] and '[close-NVDA]' in every[1]
    assert 'PM (Tech) · SubmitOrder' in every[2] and '[order-NVDA]' in every[2]

    nvda = read(symbol='nvda').payload.splitlines()

    assert len(nvda) == 2 and all('NVDA' in line for line in nvda)
    assert len(read(limit=1).payload.splitlines()) == 1
    assert read(symbol='AVGO').payload == 'No trades in AVGO yet.'
