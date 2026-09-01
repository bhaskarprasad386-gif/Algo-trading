from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class MarketSession:
    """Exchange-session gate used before starting live market data."""

    open_time: time = time(9, 15)
    close_time: time = time(15, 30)
    holidays: frozenset[date] = frozenset()

    def is_holiday(self, day: date) -> bool:
        return day in self.holidays or day.weekday() >= 5

    def is_open(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        if self.is_holiday(now.date()):
            return False
        return self.open_time <= now.time() < self.close_time


def should_start_live_data(
    now: Optional[datetime] = None,
    holidays: Iterable[date] = (),
    session: Optional[MarketSession] = None,
) -> bool:
    """Return whether the live WebSocket is allowed to start."""
    if session is None:
        session = MarketSession(holidays=frozenset(holidays))
    return session.is_open(now)
