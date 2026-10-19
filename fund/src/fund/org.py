"""The fund's Organization: registers every member at construction and runs mandates through the CIO.

Built once at import as `org`, so `.env` must be loaded before importing this module
(the members read credentials at import).
"""
from __future__ import annotations

from architectures import Organization

from fund.agents.cio import ROLE as CIO_ROLE
from fund.agents.cio import SectorAllocation, cio

NAME = 'fund'
GOAL = 'find me a good investment'


#     ================================
# --> FundOrganization
#     ================================


class Fund(Organization):
    """The fund's org: the CIO leads and receives every kickoff."""

    def __init__(self) -> None:
        super().__init__(name=NAME, goal=GOAL)

        self.cio_id = self.register_agent(CIO_ROLE, cio)
