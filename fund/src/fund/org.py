"""The fund's Organization: its members, and a record of every trade they make.

    FundOrganization()
      ├─ registers the tech PM            ──► record_trades: every accepted order lands in `trades`; GetTrades
      ├─ registers the 4 tech analysts    ──► GetTrades: reads `trades`
      ├─ PM system prompt += <analysts>    roles + member ids
      └─ trades: {member id: [Trade, …]}

`org` is the one instance per process (the PM agent is a module-level singleton, so it can only
be registered once). Import this module only after `.env` is loaded: the PM reads its credentials
at import.
"""
from __future__ import annotations

import uuid
from typing import Any

from agent_harness.tooling.decorator import bind_tool
from architectures import Organization

from fund.agents.pods.tech.analysts import ANALYSTS
from fund.agents.pods.tech.pm import ROLE as PM_ROLE
from fund.agents.pods.tech.pm import pm
from fund.trades import Trade, get_trades, record_trades

FUND_NAME = 'fund'
FUND_GOAL = 'Manage the fund portfolio to beat SPY over time with disciplined, thesis-driven risk.'


class FundOrganization(Organization):
    """The fund's members, plus every trade they place, keyed by member id."""

    def __init__(self) -> None:
        super().__init__(name=FUND_NAME, goal=FUND_GOAL)
        
        self.trades: dict[uuid.UUID, list[Trade]] = {}
        self.pm_id = self._register_trader(PM_ROLE, pm)
        self.analyst_ids = [self._register_analyst(role, agent) for role, agent in ANALYSTS]

        # The PM's team, with ids, so it can SendMessage without calling GetOrgInfo first
        pm.extend_system_prompt(self._analysts_block())

    def _analysts_block(self) -> str:
        """The PM's analysts as a system-prompt block: one line per analyst, role and member id."""
        lines = [f'- {self.agents[analyst_id].role}: {analyst_id}' for analyst_id in self.analyst_ids]

        return '<analysts>\nYour analysts (send work to them with SendMessage, by id):\n' + '\n'.join(lines) + '\n</analysts>'

    def _register_trader(self, role: str, agent: Any) -> uuid.UUID:
        """Register a member that trades, record each trade it gets accepted, and let it read the record."""
        # register agent using the org.register_agent method
        member_id = self.register_agent(role, agent)
        self.trades[member_id] = []

        # record each trade that the agent gets accepted
        record_trades(agent, self.trades[member_id], member_id, role)

        agent.add_tool(bind_tool(get_trades, _trades=self.trades))

        # return the agents id to the caller
        return member_id

    def _register_analyst(self, role: str, agent: Any) -> uuid.UUID:
        """Register a member that researches, and let it read the fund's trades."""
        member_id = self.register_agent(role, agent)

        agent.add_tool(bind_tool(get_trades, _trades=self.trades))

        return member_id


org = FundOrganization()
