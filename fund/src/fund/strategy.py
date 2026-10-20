"""Session runner: wakes the fund's agents around each NYSE trading session.

`Strategy` owns *when* things run; a subclass owns *what* runs. The schedule comes from the exchange
calendar (`exchange_calendars`, offline: holidays and early closes included), not from a broker, so the
runner needs no trading client: the agents trade on their own with the keys in their sandbox env.

    one session (e.g. 2026-11-27, an early close)
    ─────────────────────────────────────────────────────────────────────────────►  time (ET)
      09:00               09:30 ──── sleeptime ──── … ───── 13:00       13:15
        │                   │                                  │           │
    before_trading_day   on_iteration  on_iteration  …        close   after_trading_day
    (open − before_open) (every sleeptime while open)                  (close + after_close)

Every hook runs to completion before the next one is scheduled, so hooks never overlap. A hook whose
time has already passed when the runner reaches it (late start, long iteration) runs immediately; a
session that has fully ended is skipped. `stop()` interrupts any wait and exits after the running hook.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import exchange_calendars as xcals
import pandas as pd

if TYPE_CHECKING:
    from fund.org import Fund


#     ================================
# --> Helper funcs
#     ================================


@dataclass(frozen=True)
class TradingSession:
    """One exchange session: its date and its open/close as timezone-aware UTC datetimes."""

    date: pd.Timestamp
    open: datetime
    close: datetime


def _next_session(calendar_name: str, after: datetime) -> TradingSession:
    """The first session on `calendar_name` that is today or later and still open after `after`."""
    # Rebuilt on every call: a calendar only covers sessions up to a year past its construction
    calendar = xcals.get_calendar(calendar_name, start=after.date() - timedelta(days=7))
    session = calendar.date_to_session(after.date(), direction='next')

    while calendar.session_close(session).to_pydatetime() <= after:
        session = calendar.next_session(session)

    return TradingSession(
        date=session,
        open=calendar.session_open(session).to_pydatetime(),
        close=calendar.session_close(session).to_pydatetime(),
    )


#     ================================
# --> Strategy
#     ================================


class Strategy(ABC):
    """Runs before_trading_day → on_iteration (every `sleeptime` while open) → after_trading_day, per session."""

    calendar_name: str = 'XNYS'
    before_open: timedelta = timedelta(minutes=30)
    sleeptime: timedelta = timedelta(hours=1)
    after_close: timedelta = timedelta(minutes=15)

    def __init__(self) -> None:
        self._stop_event = threading.Event()

    @abstractmethod
    def before_trading_day(self, session: TradingSession) -> None:
        """Runs once, `before_open` ahead of the session's open."""

    @abstractmethod
    def after_trading_day(self, session: TradingSession) -> None:
        """Runs once, `after_close` past the session's close."""
    
    @abstractmethod
    def on_iteration(self, session: TradingSession, now: datetime) -> None:
        """Runs at the open, then every `sleeptime` until the close."""

    def stop(self) -> None:
        """Ask the runner to exit: interrupts a wait immediately, a running hook at its end."""
        self._stop_event.set()

    def _run_session(self, session: TradingSession) -> None:
        if not self._wait_until(session.open - self.before_open):
            return

        self.before_trading_day(session)

        next_iteration = session.open

        while next_iteration < session.close:
            if not self._wait_until(next_iteration):
                return

            now = datetime.now(UTC)

            self.on_iteration(session, now)

            # Scheduled from when the iteration started: a long run eats into the gap, never stacks
            next_iteration = max(now + self.sleeptime, datetime.now(UTC))

        if not self._wait_until(session.close + self.after_close):
            return

        self.after_trading_day(session)

    def _wait_until(self, wake_at: datetime) -> bool:
        """Sleep until `wake_at`; False when `stop()` cut the wait short."""
        seconds = (wake_at - datetime.now(UTC)).total_seconds()

        # Event.wait instead of time.sleep: stop() wakes it immediately
        return not self._stop_event.wait(max(seconds, 0))
    
    def run_forever(self) -> None:
        """Run every session in turn until `stop()`."""
        while not self._stop_event.is_set():
            # Look past after_close so a session whose wrap-up is still due is not skipped
            session = _next_session(self.calendar_name, datetime.now(UTC) - self.after_close)

            self._run_session(session)


#     ================================
# --> FundStrategy
#     ================================


class FundStrategy(Strategy):
    """The fund's trading day: a pre-market plan, one iteration at the open, an end-of-day review.

    Every kickoff goes to the CIO, which delegates from there.
    """

    # Longer than any session: exactly one on_iteration, at the open
    sleeptime = timedelta(days=1)
    MAX_MESSAGES = 60

    def __init__(self, org: Fund) -> None:
        super().__init__()
        self._org = org

    def before_trading_day(self, session: TradingSession) -> None:
        """Pre-market: review overnight news and open positions, set the pod limits for the day."""
        self._kickoff(
            f'Pre-market for {session.date:%Y-%m-%d} (opens {session.open:%H:%M} UTC). Review overnight '
            'news and the open positions, set the pod limits, and have the Technology PM plan its trades.'
        )

    def on_iteration(self, session: TradingSession, now: datetime) -> None:
        """Market open: the PM executes the day's decisions."""
        self._kickoff(
            f'Market open {now:%Y-%m-%d %H:%M} UTC. Have the Technology PM place the trades it planned '
            'and act on any position update.'
        )

    def after_trading_day(self, session: TradingSession) -> None:
        """After the close: review the day's trades and record outcomes and lessons."""
        self._kickoff(
            f'Market closed {session.close:%Y-%m-%d %H:%M} UTC. Review the day\'s trades and positions, '
            'record outcomes and lessons, and set the watchlist for tomorrow.'
        )

    def _kickoff(self, content: str) -> None:
        # Blocks until the org is idle: every member's inbox drained
        self._org.run([(self._org.cio_id, content)], max_messages=self.MAX_MESSAGES)
