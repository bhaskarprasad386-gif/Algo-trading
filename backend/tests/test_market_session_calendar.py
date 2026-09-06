from datetime import date, datetime, timezone

from app.backtesting.market_session_calendar import MARKET_TZ, MarketSessionCalendar


def test_weekends_are_not_sessions():
    calendar = MarketSessionCalendar()
    sessions = calendar.sessions(
        datetime(2026, 1, 3, tzinfo=timezone.utc),
        datetime(2026, 1, 5, 23, tzinfo=timezone.utc),
    )
    assert len(sessions) == 1


def test_explicit_holiday_is_excluded():
    calendar = MarketSessionCalendar(holidays=frozenset({date(2026, 1, 5)}))
    sessions = calendar.sessions(
        datetime(2026, 1, 5, tzinfo=timezone.utc),
        datetime(2026, 1, 6, tzinfo=timezone.utc),
    )
    assert len(sessions) == 1


def test_regular_session_is_915_to_1530_ist():
    calendar = MarketSessionCalendar()
    sessions = calendar.sessions(
        datetime(2026, 1, 5, tzinfo=MARKET_TZ),
        datetime(2026, 1, 5, 23, tzinfo=MARKET_TZ),
    )
    assert len(sessions) == 1
    assert sessions[0].start_ns < sessions[0].end_ns
