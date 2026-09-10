from datetime import date, time

import pytest

from app.backtesting.trading_calendar import TradingCalendar


def test_weekend_and_explicit_closed_date_are_not_trading_days():
    calendar = TradingCalendar(closed_dates=frozenset({date(2026, 1, 26)}))

    assert not calendar.is_trading_day(date(2026, 1, 24))
    assert not calendar.is_trading_day(date(2026, 1, 26))
    assert calendar.is_trading_day(date(2026, 1, 27))


def test_session_boundaries_are_exact_and_nanosecond_based():
    calendar = TradingCalendar(session_open=time(9, 15), session_close=time(15, 30))

    session = calendar.session_for(date(2026, 1, 27))

    assert session is not None
    assert session.trading_date == date(2026, 1, 27)
    assert session.end_ns > session.start_ns
    assert session.end_ns - session.start_ns == 6 * 60 * 60 * 1_000_000_000 + 15 * 60 * 1_000_000_000


def test_sessions_between_skips_closed_dates_without_inventing_events():
    calendar = TradingCalendar(closed_dates=frozenset({date(2026, 1, 26)}))

    sessions = calendar.sessions_between(date(2026, 1, 24), date(2026, 1, 28))

    assert [session.trading_date for session in sessions] == [
        date(2026, 1, 27),
        date(2026, 1, 28),
    ]


def test_reversed_date_range_is_rejected():
    with pytest.raises(ValueError, match="end_date"):
        TradingCalendar().sessions_between(date(2026, 1, 28), date(2026, 1, 27))
