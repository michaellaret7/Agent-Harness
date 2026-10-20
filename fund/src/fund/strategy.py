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
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

import exchange_calendars as xcals
import pandas as pd
from architectures import Organization

log = logging.getLogger(__name__)


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
    def initialize(self) -> None:
        """Runs once per process, as soon as `run_forever()` starts, before any session hook."""

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
        """Run one hook, logging its start, its duration, and any exception (which still propagates)."""
        log.info('%s: %s started', type(self).__name__, name)
        started = time.monotonic()

        try:
            hook(*args)
        except Exception:
            log.exception('%s: %s failed after %.1fs', type(self).__name__, name, time.monotonic() - started)
            raise

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

        self._call_hook('initialize', self.initialize)

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
        self.org = Organization()
    
    def initialize(self) -> None:
        pass

    def before_trading_day(self, session: TradingSession) -> None:
        pass

    def after_trading_day(self, session: TradingSession) -> None:
        pass

    def on_iteration(self, session: TradingSession, now: datetime) -> None:
        pass

