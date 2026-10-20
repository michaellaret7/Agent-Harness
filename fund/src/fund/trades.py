"""The fund's trade record: every order a member gets accepted through its trading tools, and a tool to read it.

    record_trades(agent, trades, …)  ──► tool_end hook on SubmitOrder · ReplaceOrder · ClosePosition
                                          status ok? ──► trades.append(Trade(..., order=ToolResult.data))

    GetTrades(symbol, limit)         ──► the record, newest first, one line per trade
                                          (bound to the org's `trades` dict with `bind_tool(get_trades, _trades=...)`)

Kept apart from `fund.org` so it imports no member (and so reads no credentials).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from agent_harness.hooks import HookContext
from agent_harness.tooling.decorator import Param, agent_tool
from agent_harness.tooling.result import ToolResult

# Tools that send an order to the account; reads and cancels are not trades
TRADE_TOOLS = ('SubmitOrder', 'ReplaceOrder', 'ClosePosition')


@dataclass(frozen=True)
class Trade:
    """One accepted trading-tool call: what the member asked for and the full order the broker returned."""

    member_id: uuid.UUID
    role: str
    tool: str
    args: dict[str, Any]
    order: dict[str, Any]  # the Alpaca order as JSON (id, symbol, qty, status, fills, legs, …), from ToolResult.data
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


#     ================================
# --> Helper funcs
#     ================================


def _amount(order: dict[str, Any]) -> str:
    """The order's size: shares, or dollars for a notional order."""
    if order.get('notional') is not None:
        return f'${order["notional"]}'

    return f'{order.get("qty")} sh'


def _trade_line(trade: Trade) -> str:
    """One trade as the model sees it: when, who, what, and the order's id and status when placed."""
    order = trade.order

    line = (
        f'- {trade.at:%Y-%m-%d %H:%M} UTC · {trade.role} · {trade.tool}: '
        f'{order.get("side")} {_amount(order)} {order.get("symbol")} {order.get("type")} '
        f'({order.get("order_class")}) [{order.get("id")}] — {order.get("status")} when placed'
    )

    if order.get('client_order_id'):
        line += f', client id {order["client_order_id"]}'

    return line


#     ================================
# --> Record and tool
#     ================================


def record_trades(agent: Any, trades: list[Trade], member_id: uuid.UUID, role: str) -> None:
    """Append each trade `agent` gets accepted to `trades`. The agent must have the trading tools."""

    def record_trade(ctx: HookContext) -> None:
        # Rejected or invalid orders are not trades
        if ctx.outcome is None or ctx.outcome.status != 'ok':
            return

        # Every trading tool returns its order as data; a missing one is a tool bug, not a trade to drop silently
        if not isinstance(ctx.outcome.data, dict):
            raise TypeError(f'{ctx.tool_name} succeeded without returning its order as ToolResult.data')

        trade = Trade(
            member_id=member_id,
            role=role,
            tool=ctx.tool_name or '',
            args=ctx.args or {},
            order=ctx.outcome.data,
        )

        trades.append(trade)

    agent.add_hook('tool_end', record_trade, tool=TRADE_TOOLS)


@agent_tool(name='GetTrades', safe_parallel=True)
def get_trades(
    symbol: Annotated[str | None, Param(description='Only trades in this ticker, e.g. "NVDA". Omit for all.')] = None,
    limit: Annotated[int, Param(description='Most recent trades to return.')] = 20,
    _trades: dict[uuid.UUID, list[Trade]] | None = None,
) -> ToolResult:
    """The fund's trades this session, newest first: every order a PM placed, replaced, or closed, with its id.

    Status is the order's status when it was placed; fills and triggered stops come later and are not shown.
    """
    assert _trades is not None, 'GetTrades must be bound to the org trade record'

    wanted = symbol.strip().upper() if symbol else None

    # Oldest first (stable, so trades with the same timestamp keep record order), then flipped to newest first
    trades = sorted(
        (trade for member_trades in _trades.values() for trade in member_trades
         if wanted is None or trade.order.get('symbol') == wanted),
        key=lambda trade: trade.at,
    )[::-1][:limit]

    if not trades:
        return ToolResult(f'No trades{f" in {wanted}" if wanted else ""} yet.', status='ok')

    return ToolResult('\n'.join(_trade_line(trade) for trade in trades), status='ok')
