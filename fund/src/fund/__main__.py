"""Fund entry point: owns bootstrap (`load_dotenv`, logging), then runs the orchestrator until stopped.

    uv run --package fund python -m fund
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv


def main() -> None:
    """Load configuration, then wake the fund's org around every NYSE session."""
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    # Imported after load_dotenv: building the fund reads credentials at import
    from fund.strategy import FundOrchestrator

    FundOrchestrator().run()


if __name__ == '__main__':
    main()
