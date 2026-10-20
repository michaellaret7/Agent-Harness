"""Session runner: wakes the fund's agents around each NYSE trading session.

`Strategy` owns *when* things run; a subclass owns *what* runs. The schedule comes from the exchange
calendar (`exchange_calendars`, offline: holidays and early closes included), not from a broker, so the
runner needs no trading client: the agents trade on their own with the keys in their sandbox env.

`initialize()` runs once, the moment `run()` starts, whatever the time; then the session loop:

    one session (e.g. 2026-11-27, an early close)
    ─────────────────────────────────────────────────────────────────────────────►  time (ET)
      09:00               09:30 ──── sleeptime ──── … ───── 13:00       13:15
        │                   │                                  │           │
    before_trading_day   on_iteration  on_iteration  …        close   after_trading_day
    (open − before_open) (every sleeptime while open)                  (close + after_close)

Every hook runs to completion before the next one is scheduled, so hooks never overlap. A hook whose
time has already passed when the runner reaches it (late start, long iteration) runs immediately; a
session that has fully ended is skipped. `stop()` interrupts any wait and exits after the running hook.
A session hook that raises is logged and skipped, and the schedule carries on; `initialize` fails fast.
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from fund.org import org

log = logging.getLogger(__name__)

EASTERN = ZoneInfo('America/New_York')

#     ================================
# --> Helper funcs
#     ================================


def _eastern(moment: datetime) -> str:
    """`moment` as NYSE wall-clock time, e.g. 'Tue 2026-10-21 11:30 ET'."""
    return f'{moment.astimezone(EASTERN):%a %Y-%m-%d %H:%M} ET'


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
    def initialize(self) -> None:
        """Runs once per process, as soon as `run()` starts, before any session hook. Optional: a no-op by default."""

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
        log.info('%s: stop requested', type(self).__name__)

        self._stop_event.set()

    def _run_session(self, session: TradingSession) -> None:
        if not self._wait_until(session.open - self.before_open, 'before_trading_day'):
            return

        self._call_hook('before_trading_day', self.before_trading_day, session)

        next_iteration = session.open

        while next_iteration < session.close:
            if not self._wait_until(next_iteration, 'on_iteration'):
                return

            now = datetime.now(UTC)

            self._call_hook('on_iteration', self.on_iteration, session, now)

            # Scheduled from when the iteration started: a long run eats into the gap, never stacks
            next_iteration = max(now + self.sleeptime, datetime.now(UTC))

        if not self._wait_until(session.close + self.after_close, 'after_trading_day'):
            return

        self._call_hook('after_trading_day', self.after_trading_day, session)

    def _call_hook(self, name: str, hook: Callable[..., None], *args: Any) -> None:
        """Run one session hook, logging its start, its duration, and any exception.

        A failed hook is logged and skipped, so one bad hour (a provider timeout, a broker 5xx)
        never ends the runner.
        """
        log.info('%s: %s started', type(self).__name__, name)
        started = time.monotonic()

        try:
            hook(*args)
        except Exception:
            log.exception('%s: %s failed after %.1fs', type(self).__name__, name, time.monotonic() - started)

            return

        log.info('%s: %s finished in %.1fs', type(self).__name__, name, time.monotonic() - started)

    def _wait_until(self, wake_at: datetime, next_hook: str) -> bool:
        """Sleep until `wake_at`; False when `stop()` cut the wait short."""
        seconds = (wake_at - datetime.now(UTC)).total_seconds()

        if seconds > 0:
            log.info(
                '%s: sleeping %.0fs until %s UTC for %s',
                type(self).__name__, seconds, f'{wake_at:%Y-%m-%d %H:%M:%S}', next_hook,
            )

        # Event.wait instead of time.sleep: stop() wakes it immediately
        return not self._stop_event.wait(max(seconds, 0))

    def run(self) -> None:
        """Run `initialize()` once, then every session in turn until `stop()`."""
        log.info('%s: starting (calendar %s)', type(self).__name__, self.calendar_name)

        # Called directly, not through _call_hook: a failed startup should stop the process
        self.initialize()

        while not self._stop_event.is_set():
            # Look past after_close so a session whose wrap-up is still due is not skipped
            session = _next_session(self.calendar_name, datetime.now(UTC) - self.after_close)

            log.info(
                '%s: next session %s, open %s UTC, close %s UTC',
                type(self).__name__, f'{session.date:%Y-%m-%d}', f'{session.open:%H:%M}', f'{session.close:%H:%M}',
            )

            self._run_session(session)

        log.info('%s: stopped', type(self).__name__)


#     ================================
# --> FundStrategy
#     ================================


class FundOrchestrator(Strategy):
    """NYSE session: 7:30 a.m. ET, then hourly from the 9:30 open until an hour after the close."""

    before_open = timedelta(hours=2)   # 09:30 open → 07:30 ET
    sleeptime = timedelta(hours=1)
    after_close = timedelta(hours=1)   # 16:00 close → 17:00 ET

    def __init__(self) -> None:
        super().__init__()
        self.org = org
    
    def initialize(self) -> None:
        pass

    def before_trading_day(self, session: TradingSession) -> None:
        self._kickoff(
            f'Pre-market, {_eastern(datetime.now(UTC))}. The session opens {_eastern(session.open)} '
            f'and closes {_eastern(session.close)}. Run your pre-market routine.'
        )

    def after_trading_day(self, session: TradingSession) -> None:
        self._kickoff(
            f'Post-close, {_eastern(datetime.now(UTC))}. The session closed {_eastern(session.close)}. '
            'Run your post-close routine and write your journal entry.'
        )

    def on_iteration(self, session: TradingSession, now: datetime) -> None:
        self._kickoff(
            f'Intraday, {_eastern(now)}. The session closes {_eastern(session.close)}. '
            'Check what changed since your last check and act on it.'
        )

    def _kickoff(self, content: str) -> None:
        """Send `content` to the PM and block until the org is idle."""
        self.org.run([(self.org.pm_id, content)])

