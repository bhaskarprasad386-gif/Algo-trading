from datetime import datetime, timezone

import pytest

from app.backtesting.nse_session_calendars import (
    nse_calendar_for_instrument_2026,
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
    assert windows[0].end_ns == _ns(datetime(2026, 2, 18, 10, 09, tzinfo=timezone.utc))


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
