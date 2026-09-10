"""Provider-agnostic trading calendar primitives for historical coverage.

The calendar deliberately does not embed an exchange holiday table. An exchange
adapter can supply closed dates and session windows without coupling the
backtesting engine to a particular venue or year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone


@dataclass(frozen=True)
class TradingSession:
    """One continuous UTC session window identified by its trading date."""

    trading_date: date
    start_ns: int
    end_ns: int


@dataclass(frozen=True)
class TradingCalendar:
    """Calendar rules independent of instrument or data cadence."""

    session_open: time = time(9, 15)
    session_close: time = time(15, 30)
    closed_dates: frozenset[date] = frozenset()

    def is_trading_day(self, trading_date: date) -> bool:
        """Return false for weekends and explicitly closed exchange dates."""
        return trading_date.weekday() < 5 and trading_date not in self.closed_dates

    def session_for(self, trading_date: date) -> TradingSession | None:
        """Return the session for a trading day, or None when the day is closed."""
        if not self.is_trading_day(trading_date):
            return None
        start = datetime.combine(trading_date, self.session_open, tzinfo=timezone.utc)
        end = datetime.combine(trading_date, self.session_close, tzinfo=timezone.utc)
        return TradingSession(trading_date, _to_ns(start), _to_ns(end))

    def sessions_between(self, start_date: date, end_date: date) -> tuple[TradingSession, ...]:
        """Return sessions in inclusive date order without inventing data events."""
        if end_date < start_date:
            raise ValueError("end_date must not be before start_date")
        sessions: list[TradingSession] = []
        current = start_date
        while current <= end_date:
            session = self.session_for(current)
            if session is not None:
                sessions.append(session)
            current += timedelta(days=1)
        return tuple(sessions)


def _to_ns(value: datetime) -> int:
    return value.astimezone(timezone.utc).timestamp_ns()
