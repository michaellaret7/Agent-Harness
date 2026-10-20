"""Fund-specific tools (beyond the workspace's ExecuteCode and memory) registered on members.

    trading_tools(client)  ──► SubmitOrder · GetOrders · ReplaceOrder · CancelOrder      (orders.py)
                               GetAccount · GetPositions · ClosePosition                 (positions.py)
"""
from __future__ import annotations

from typing import Any

from alpaca.trading.client import TradingClient

from fund.tools.broker import paper_client
from fund.tools.orders import order_tools
from fund.tools.positions import position_tools


def trading_tools(client: TradingClient) -> list[dict[str, Any]]:
    """Every paper-trading tool a PM gets, bound to one shared `client`."""
    return [*order_tools(client), *position_tools(client)]


__all__ = ['paper_client', 'trading_tools']
