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
        start_local = start.astimezone(MARKET_TZ).date()
        end_local = end.astimezone(MARKET_TZ).date()
        result: list[SessionWindow] = []
        day = start_local
        while day <= end_local:
            special = self.special_sessions.get(day)
            if special is not None:
                session_start, session_end = special
            elif day.weekday() < 5 and day not in self.holidays:
                session_start, session_end = self.weekday_start, self.weekday_end
            else:
                day += timedelta(days=1)
                continue
            result.append(
                SessionWindow(
                    self._ns(datetime.combine(day, session_start, tzinfo=MARKET_TZ)),
                    self._ns(datetime.combine(day, session_end, tzinfo=MARKET_TZ)),
                )
            )
            day += timedelta(days=1)
        return tuple(result)
