"""Live-session calendars for Calendar Spread's futures exchanges.

Holiday dates are exchange trading holidays, not clearing/settlement holidays.
Unknown calendar years fail closed until the exchange's official schedule is
added. All datetimes passed to this module must be timezone-aware IST values.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE F&O holidays for 2026 (including the Jan 15 amendment).
NFO_HOLIDAYS_2026 = frozenset({
    date(2026, 1, 15), date(2026, 1, 26), date(2026, 3, 3),
    date(2026, 3, 26), date(2026, 3, 31), date(2026, 4, 3),
    date(2026, 4, 14), date(2026, 5, 1), date(2026, 5, 28),
    date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2),
    date(2026, 10, 20), date(2026, 11, 10), date(2026, 11, 24),
    date(2026, 12, 25),
})
# BSE equity derivatives 2026 holidays; maintained independently from NFO.
BFO_HOLIDAYS_2026 = frozenset({
    date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26),
    date(2026, 3, 31), date(2026, 4, 3), date(2026, 4, 14),
    date(2026, 5, 1), date(2026, 5, 28), date(2026, 6, 26),
    date(2026, 9, 14), date(2026, 10, 2), date(2026, 10, 20),
    date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
})

# MCX holiday session policy: "both" closes the full day, "evening" permits
# only 17:00-23:30, and "morning" permits only 09:00-17:00.
MCX_HOLIDAY_SESSIONS_2026 = {
    date(2026, 1, 1): "morning",
    date(2026, 1, 26): "both",
    date(2026, 3, 3): "evening",
    date(2026, 3, 26): "evening",
    date(2026, 3, 31): "evening",
    date(2026, 4, 3): "both",
    date(2026, 4, 14): "evening",
    date(2026, 5, 1): "evening",
    date(2026, 5, 28): "evening",
    date(2026, 6, 26): "evening",
    date(2026, 9, 14): "evening",
    date(2026, 10, 2): "both",
    date(2026, 10, 20): "evening",
    date(2026, 11, 10): "evening",
    date(2026, 11, 24): "evening",
    date(2026, 12, 25): "both",
}
# Muhurat sessions for 2026 were announced without exact timings; fail closed.
UNSCHEDULED_SPECIAL_SESSION_DATES = frozenset({date(2026, 11, 8)})
SUPPORTED_YEARS = frozenset({2026})


def _mcx_close_time(day: date) -> time:
    """MCX non-agri close follows US DST: 23:30 in DST, 23:55 otherwise."""
    if date(2026, 3, 8) <= day < date(2026, 11, 1):
        return time(23, 30)
    return time(23, 55)


def exchange_is_open(exchange: str, value: datetime) -> bool:
    """Return whether an exchange is in its announced regular session."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("live exchange calendar requires a timezone-aware datetime")
    local = value.astimezone(IST)
    exchange = exchange.strip().upper()
    if exchange not in {"NFO", "BFO", "MCX"}:
        return False
    if local.year not in SUPPORTED_YEARS:
        return False
    day = local.date()
    if local.weekday() >= 5 or day in UNSCHEDULED_SPECIAL_SESSION_DATES:
        return False

    if exchange == "MCX":
        holiday_session = MCX_HOLIDAY_SESSIONS_2026.get(day)
        if holiday_session == "both":
            return False
        if holiday_session == "morning":
            return time(9, 0) <= local.time() < time(17, 0)
        if holiday_session == "evening":
            return time(17, 0) <= local.time() <= _mcx_close_time(day)
        return time(9, 0) <= local.time() <= _mcx_close_time(day)

    holidays = NFO_HOLIDAYS_2026 if exchange == "NFO" else BFO_HOLIDAYS_2026
    if day in holidays:
        return False
    return time(9, 15) <= local.time() <= time(15, 30)


def any_exchange_open(value: datetime) -> bool:
    """True when at least one subscribed exchange has an open session."""
    return any(exchange_is_open(exchange, value) for exchange in ("NFO", "BFO", "MCX"))
