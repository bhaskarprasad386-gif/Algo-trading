"""Exchange-session windows used by resumable historical-data completeness checks."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .session_gap_planner import SessionWindow


MARKET_TZ = ZoneInfo("Asia/Kolkata")


class MarketSessionCalendar:
    """Build regular weekday sessions with an explicit holiday/closure set.

    Holiday data is deliberately injected rather than guessed. Session endpoints
    are inclusive and use 15:29 for intraday bars so a 1-minute cadence represents
    the regular NSE/NFO timestamps without inventing a 15:30 bar.
    """

    def __init__(
        self,
        *,
        holidays: frozenset[date] = frozenset(),
        weekday_start: time = time(9, 15),
        weekday_end: time = time(15, 29),
    ) -> None:
        if weekday_start >= weekday_end:
            raise ValueError("session start must be before session end")
        self.holidays = holidays
        self.weekday_start = weekday_start
        self.weekday_end = weekday_end

    @staticmethod
    def _ns(value: datetime) -> int:
        return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)

    def sessions(self, start: datetime, end: datetime) -> tuple[SessionWindow, ...]:
        """Return weekday sessions intersecting the requested market-time range."""
        if end < start:
            raise ValueError("end must not precede start")
        start_local = start.astimezone(MARKET_TZ).date()
        end_local = end.astimezone(MARKET_TZ).date()
        result: list[SessionWindow] = []
        day = start_local
        while day <= end_local:
            if day.weekday() < 5 and day not in self.holidays:
                session_start = datetime.combine(day, self.weekday_start, tzinfo=MARKET_TZ)
                session_end = datetime.combine(day, self.weekday_end, tzinfo=MARKET_TZ)
                result.append(SessionWindow(self._ns(session_start), self._ns(session_end)))
            day += timedelta(days=1)
        return tuple(result)
