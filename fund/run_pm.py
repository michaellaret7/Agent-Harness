"""Run the Technology PM on its own, without the Organization: load `.env`, send the task, print the reply.

    uv run --package fund python fund/run_pm.py
"""
from __future__ import annotations

from dotenv import load_dotenv

TASK = 'Test that the alpaca api keys are working, then do some portfolio analytics'


def main() -> None:
    """Load configuration, then hand the task straight to the PM."""
    load_dotenv()

    # Imported after load_dotenv: building the PM reads credentials at import
    from fund.agents.pods.tech.pm import pm

    print(pm.run(TASK))


if __name__ == '__main__':
    main()
