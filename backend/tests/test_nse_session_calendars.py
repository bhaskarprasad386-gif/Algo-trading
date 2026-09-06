from datetime import datetime, timezone

import pytest

from app.backtesting.nse_session_calendars import (
    nse_calendar_for_instrument_2026,
    nse_session_windows,
    nse_session_windows_2026,
)


def _ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def test_equity_holiday_is_excluded():
    calendar = nse_calendar_for_instrument_2026("NSE:3045:SBIN")
    windows = calendar.sessions(
        datetime(2026, 1, 15, tzinfo=timezone.utc),
        datetime(2026, 1, 15, 23, 59, tzinfo=timezone.utc),
    )
    assert windows == ()


def test_2025_equity_holiday_is_excluded():
    request = type("R", (), {
        "timeframe": "1m",
        "start_ns": _ns(datetime(2025, 2, 26, tzinfo=timezone.utc)),
        "end_ns": _ns(datetime(2025, 2, 26, 23, 59, tzinfo=timezone.utc)),
        "instrument": "NSE:3045:SBIN",
    })()
    assert nse_session_windows(request) == ()


def test_2025_and_2026_holiday_sets_are_not_mixed():
    holiday_2025 = type("R", (), {
        "timeframe": "1m",
        "start_ns": _ns(datetime(2025, 2, 26, tzinfo=timezone.utc)),
        "end_ns": _ns(datetime(2025, 2, 26, 23, 59, tzinfo=timezone.utc)),
        "instrument": "NSE:3045:SBIN",
    })()
    regular_2026 = type("R", (), {
        "timeframe": "1m",
        "start_ns": _ns(datetime(2026, 2, 26, tzinfo=timezone.utc)),
        "end_ns": _ns(datetime(2026, 2, 26, 23, 59, tzinfo=timezone.utc)),
        "instrument": "NSE:3045:SBIN",
    })()
    assert nse_session_windows(holiday_2025) == ()
    assert len(nse_session_windows(regular_2026)) == 1


def test_year_boundary_uses_both_calendars():
    request = type("R", (), {
        "timeframe": "1m",
        "start_ns": _ns(datetime(2025, 12, 31, tzinfo=timezone.utc)),
        "end_ns": _ns(datetime(2026, 1, 2, 23, 59, tzinfo=timezone.utc)),
        "instrument": "NSE:3045:SBIN",
    })()
    windows = nse_session_windows(request)
    assert len(windows) == 3
    assert [w.start_ns for w in windows] == [
        _ns(datetime(2025, 12, 31, 3, 45, tzinfo=timezone.utc)),
        _ns(datetime(2026, 1, 1, 3, 45, tzinfo=timezone.utc)),
        _ns(datetime(2026, 1, 2, 3, 45, tzinfo=timezone.utc)),
    ]


def test_fno_and_equity_holiday_sets_are_segment_specific():
    equity = nse_calendar_for_instrument_2026("NSE:3045:SBIN")
    future = nse_calendar_for_instrument_2026("NFO:101:SBINJAN")
    day_start = datetime(2026, 2, 19, tzinfo=timezone.utc)
    day_end = datetime(2026, 2, 19, 23, 59, tzinfo=timezone.utc)
    assert equity.sessions(day_start, day_end) == ()
    assert len(future.sessions(day_start, day_end)) == 1


def test_future_regular_session_ends_at_1539_ist():
    calendar = nse_calendar_for_instrument_2026("NFO:101:SBINJAN")
    windows = calendar.sessions(
        datetime(2026, 2, 18, tzinfo=timezone.utc),
        datetime(2026, 2, 18, 23, 59, tzinfo=timezone.utc),
    )
    assert len(windows) == 1
    assert windows[0].end_ns == _ns(datetime(2026, 2, 18, 10, 9, tzinfo=timezone.utc))


def test_daily_requests_do_not_use_intraday_session_windows():
    request = type("R", (), {
        "timeframe": "1d",
        "start_ns": 0,
        "end_ns": 10**18,
        "instrument": "NSE:3045:SBIN",
    })()
    assert nse_session_windows_2026(request) == ()


def test_unknown_exchange_fails_closed():
    with pytest.raises(ValueError):
        nse_calendar_for_instrument_2026("BSE:1:SBIN")


def test_unknown_year_fails_closed():
    request = type("R", (), {
        "timeframe": "1m",
        "start_ns": _ns(datetime(2024, 12, 31, tzinfo=timezone.utc)),
        "end_ns": _ns(datetime(2025, 1, 2, 23, 59, tzinfo=timezone.utc)),
        "instrument": "NSE:3045:SBIN",
    })()
    with pytest.raises(ValueError, match="unsupported NSE calendar year"):
        nse_session_windows(request)
