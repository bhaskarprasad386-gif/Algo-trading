"""Segment-aware NSE session calendars for historical completeness checks."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Iterable

from .market_session_calendar import MarketSessionCalendar
from .session_gap_planner import SessionWindow
from .nse_2026_holidays import NSE_FNO_TRADING_HOLIDAYS_2026


# NSE equity has additional 2026 holidays that are not F&O holidays.
NSE_EQUITY_TRADING_HOLIDAYS_2026 = frozenset(
    {
        date(2026, 1, 15),
        date(2026, 1, 26),
        date(2026, 2, 19),
        date(2026, 3, 3),
        date(2026, 3, 19),
        date(2026, 3, 26),
        date(2026, 3, 31),
        date(2026, 4, 1),
        date(2026, 4, 3),
        date(2026, 4, 14),
        date(2026, 5, 1),
        date(2026, 5, 28),
        date(2026, 6, 26),
        date(2026, 8, 26),
        date(2026, 9, 14),
        date(2026, 10, 2),
        date(2026, 10, 20),
        date(2026, 11, 10),
        date(2026, 11, 24),
        date(2026, 12, 25),
    }
)


def _windows(
    start: datetime,
    end: datetime,
    *,
    holidays: frozenset[date],
    close: time,
) -> tuple[SessionWindow, ...]:
    return MarketSessionCalendar(
        holidays=holidays,
        weekday_start=time(9, 15),
        weekday_end=close,
    ).sessions(start, end)


def nse_session_windows_for_request(request: object) -> Iterable[SessionWindow]:
    """Return session windows matching the instrument's exchange segment.

    NSE cash uses 09:15-15:29 for 1-minute completeness. NFO futures use
    09:15-15:39. Unknown instruments fail closed instead of guessing.
    """
    instrument = str(request.instrument)
    start_ns = int(request.start_ns)
    end_ns = int(request.end_ns)
    from datetime import timezone

    start = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)

    if instrument.startswith("NSE:"):
        return _windows(
            start, end,
            holidays=NSE_EQUITY_TRADING_HOLIDAYS_2026,
            close=time(15, 29),
        )
    if instrument.startswith("NFO:"):
        return _windows(
            start, end,
            holidays=NSE_FNO_TRADING_HOLIDAYS_2026,
            close=time(15, 39),
        )
    raise ValueError(f"unsupported NSE session instrument: {instrument}")
