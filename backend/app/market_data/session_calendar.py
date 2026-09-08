"""Trading-session helpers for NSE cash/F&O historical coverage."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

NSE_SESSION_OPEN = time(9, 15)
NSE_SESSION_CLOSE = time(15, 30)


def _day_session(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, NSE_SESSION_OPEN), datetime.combine(day, NSE_SESSION_CLOSE)


def trading_session_ranges(start: datetime, end: datetime, *, holidays: set[date] | None = None) -> list[tuple[datetime, datetime]]:
    """Return weekday NSE cash/F&O day-session windows intersecting [start, end].

    Holidays are injectable so production can use an exchange holiday calendar without
    coupling the storage layer to a particular calendar provider.
    """
    if start >= end:
        return []
    closed = holidays or set()
    day = start.date()
    last = end.date()
    result: list[tuple[datetime, datetime]] = []
    while day <= last:
        if day.weekday() < 5 and day not in closed:
            session_start, session_end = _day_session(day)
            clipped_start = max(start, session_start)
            clipped_end = min(end, session_end)
            if clipped_start < clipped_end:
                result.append((clipped_start, clipped_end))
        day += timedelta(days=1)
    return result


def session_ranges_for_instrument(start: datetime, end: datetime, *, segment: str) -> list[tuple[datetime, datetime]]:
    """Map NSE cash/NFO futures to the standard equity/F&O day session."""
    if segment.upper() not in {"NSE", "NFO"}:
        return [(start, end)] if start < end else []
    return trading_session_ranges(start, end)
