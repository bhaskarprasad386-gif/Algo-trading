from datetime import date, datetime, timezone

import pytest

from app.backtesting.nse_session_calendar import (
    NSE_EQUITY_TRADING_HOLIDAYS_2026,
    nse_session_windows_for_request,
)


def _request(instrument):
    return type("R", (), {
        "instrument": instrument,
        "start_ns": int(datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
        "end_ns": int(datetime(2026, 1, 16, tzinfo=timezone.utc).timestamp() * 1_000_000_000),
    })()


def test_equity_calendar_has_additional_cash_holiday():
    assert date(2026, 1, 15) in NSE_EQUITY_TRADING_HOLIDAYS_2026


def test_cash_session_uses_1529_end():
    windows = tuple(nse_session_windows_for_request(_request("NSE:3045:SBIN")))
    assert len(windows) == 1
    assert windows[0].end_ns - windows[0].start_ns == 374 * 60 * 1_000_000_000


def test_fno_session_uses_1539_end():
    windows = tuple(nse_session_windows_for_request(_request("NFO:101:SBINJAN")))
    assert len(windows) == 2
    assert windows[0].end_ns - windows[0].start_ns == 384 * 60 * 1_000_000_000


def test_unknown_segment_fails_closed():
    with pytest.raises(ValueError):
        tuple(nse_session_windows_for_request(_request("BSE:1:SBIN")))
