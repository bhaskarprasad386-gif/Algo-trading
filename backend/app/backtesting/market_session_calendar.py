"""Exchange-session windows used by historical-data completeness checks."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .session_gap_planner import SessionWindow


MARKET_TZ = ZoneInfo("Asia/Kolkata")


class MarketSessionCalendar:
    """Build sessions from explicit holidays plus optional special sessions."""

    def __init__(
        self,
        *,
        holidays: frozenset[date] = frozenset(),
        weekday_start: time = time(9, 15),
        weekday_end: time = time(15, 29),
        special_sessions: dict[date, tuple[time, time]] | None = None,
    ) -> None:
        if weekday_start >= weekday_end:
            raise ValueError("session start must be before session end")
        self.holidays = holidays
        self.weekday_start = weekday_start
        self.weekday_end = weekday_end
        self.special_sessions = dict(special_sessions or {})
        for day, (start_time, end_time) in self.special_sessions.items():
            if start_time >= end_time:
                raise ValueError(f"invalid special session for {day.isoformat()}")

    @staticmethod
    def _ns(value: datetime) -> int:
        return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)

    def sessions(self, start: datetime, end: datetime) -> tuple[SessionWindow, ...]:
        if end < start:
            raise ValueError("end must not precede start")
        # Requests are expressed in UTC nanoseconds.  Treat their UTC calendar
        # dates as the requested market dates; converting the end instant to IST
        # would incorrectly pull the following trading day into a full-day range.
        start_date = start.astimezone(timezone.utc).date()
        end_date = end.astimezone(timezone.utc).date()
        result: list[SessionWindow] = []
        day = start_date
        while day <= end_date:
            special = self.special_sessions.get(day)
            if special is not None:
                session_start, session_end = special
            elif day.weekday() < 5 and day not in self.holidays:
                session_start, session_end = self.weekday_start, self.weekday_end
            else:
                day += timedelta(days=1)
                continue
            window = SessionWindow(
                self._ns(datetime.combine(day, session_start, tzinfo=MARKET_TZ)),
                self._ns(datetime.combine(day, session_end, tzinfo=MARKET_TZ)),
            )
            result.append(window)
            day += timedelta(days=1)
        return tuple(result)
