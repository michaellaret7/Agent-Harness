"""Minimal repro for <BUG_DESCRIPTION>.

Goal: a single script that reliably triggers the bug. No test framework,
no fixtures, no setup — just the smallest amount of code that fails.

Usage:
    python repro.py

Expected (buggy) behavior:
    <what should happen — and what actually happens>

Once this script reproduces the bug, promote it into a regression test
under tests/ before committing the fix.
"""
from __future__ import annotations

# --- Imports under test ---
# from <package> import <thing>


# --- Inputs that trigger the bug ---
# Keep these literal and small. If the bug only fires on a real file or
# real network call, mock the boundary or commit a tiny fixture.


def main() -> None:
    # 1. Set up the minimum state needed.

    # 2. Call the suspect function with the triggering input.
    #    result = <thing>(<input>)

    # 3. Assert the *expected* behavior. The assertion should FAIL today.
    #    assert result == <expected>, f"got {result!r}"

    print("repro ran without raising — bug did not reproduce")


if __name__ == "__main__":
    main()
