from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo


MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class MarketSession:
    """Exchange-session gate used before starting live market data."""

    open_time: time = time(9, 15)
    close_time: time = time(15, 30)
    holidays: frozenset[date] = frozenset()

    def is_holiday(self, day: date) -> bool:
        return day in self.holidays or day.weekday() >= 5

    def is_open(self, now: Optional[datetime] = None) -> bool:
        """Return whether *now* falls inside the NSE session in India time.

        Naive datetimes are treated as India-local for backward compatibility;
        timezone-aware datetimes are converted to the exchange timezone.
        """
        current = now or datetime.now(MARKET_TIMEZONE)
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TIMEZONE)
        else:
            current = current.astimezone(MARKET_TIMEZONE)
        if self.is_holiday(current.date()):
            return False
        return self.open_time <= current.time() < self.close_time


def should_start_live_data(
    now: Optional[datetime] = None,
    holidays: Iterable[date] = (),
    session: Optional[MarketSession] = None,
) -> bool:
    """Return whether the live WebSocket is allowed to start."""
    if session is None:
        session = MarketSession(holidays=frozenset(holidays))
    return session.is_open(now)
