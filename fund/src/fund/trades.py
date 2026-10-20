"""The fund's trade record: every order a member gets accepted through its trading tools.

    record_trades(agent, trades, …)  ──► tool_end hook on SubmitOrder · ReplaceOrder · ClosePosition
                                          status ok? ──► trades.append(Trade(..., order=ToolResult.data))

Kept apart from `fund.org` so it imports no member (and so reads no credentials).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_harness.hooks import HookContext

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
