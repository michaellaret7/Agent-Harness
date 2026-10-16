"""The CIO member: agent + workspace config in `agent.py`, role prompt in `prompt.md`, answer schema in `output.py`, curated skills in `skills/`."""

from fund.agents.cio.agent import ROLE, cio
from fund.agents.cio.output import SectorAllocation

__all__ = ['ROLE', 'SectorAllocation', 'cio']
