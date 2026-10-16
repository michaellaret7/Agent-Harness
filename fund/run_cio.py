"""Run the CIO on its own, without the Organization: load `.env`, send the mandate, print the allocation.

    uv run --package fund python fund/run_cio.py
"""
from __future__ import annotations

from dotenv import load_dotenv

GOAL = """Construct the fund's long-term portfolio allocation. The goal is to beat SPY.

Do a deep macro dive and a full research segment, then decide the target weights, each as a percent of
the total portfolio:
- each of the 11 US equity sectors, split into a core part (strong long-term holdings) and a tactical
  part (room to trade in and out within the sector), with the favored themes inside it,
- the bond buckets and international regions held, if any,
- the thesis behind every target.

Build the portfolio you believe will beat SPY. Do not mirror SPY's weights.

The portfolio will hold stocks and ETFs (sector, theme, basket, and bond ETFs) chosen later inside your
targets. Do not pick individual names. Return the full allocation."""


def main() -> None:
    """Load configuration, then hand the mandate straight to the CIO."""
    load_dotenv()

    # Imported after load_dotenv: building the CIO reads credentials at import
    from fund.agents.cio import cio

    print(cio.run(GOAL))


if __name__ == '__main__':
    main()
