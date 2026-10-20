"""The fund's Organization: its members, and a record of every trade they make.

    FundOrganization()
      ├─ registers the tech PM
      └─ trades: {member id: [Trade, …]}   ◄── record_trades (fund/trades.py)

`org` is the one instance per process (the PM agent is a module-level singleton, so it can only
be registered once). Import this module only after `.env` is loaded: the PM reads its credentials
at import.
"""
from __future__ import annotations

import uuid
from typing import Any

from architectures import Organization

from fund.agents.pods.tech.pm import ROLE as PM_ROLE
from fund.agents.pods.tech.pm import pm
from fund.trades import Trade, record_trades

FUND_NAME = 'fund'
FUND_GOAL = 'Manage the fund portfolio to beat SPY over time with disciplined, thesis-driven risk.'


class FundOrganization(Organization):
    """The fund's members, plus every trade they place, keyed by member id."""

    def __init__(self) -> None:
        super().__init__(name=FUND_NAME, goal=FUND_GOAL)
        self.trades: dict[uuid.UUID, list[Trade]] = {}

        self.pm_id = self._register_trader(PM_ROLE, pm)

    def _register_trader(self, role: str, agent: Any) -> uuid.UUID:
        """Register a member that trades, and record each trade it gets accepted."""
        member_id = self.register_agent(role, agent)
        self.trades[member_id] = []

        record_trades(agent, self.trades[member_id], member_id, role)

        return member_id


org = FundOrganization()
