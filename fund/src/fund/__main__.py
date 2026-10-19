"""Fund entry point: owns bootstrap (`load_dotenv`), reads the mandate, and runs the Organization.

    uv run --package fund python -m fund
"""
from __future__ import annotations

from dotenv import load_dotenv


def main() -> None:
    """Load configuration, read the portfolio mandate, and hand it to the fund."""
    load_dotenv()

    # Imported after load_dotenv: building the fund reads credentials at import
    from fund.org import org

    mandate = input('Portfolio mandate: ').strip()

    allocation = org.run_fund(mandate)

    print(allocation.model_dump_json(indent=2))


if __name__ == '__main__':
    main()
