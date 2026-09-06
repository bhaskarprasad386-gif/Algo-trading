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


def test_regular_session_ends_at_1529_ist_for_intraday_bars():
    calendar = MarketSessionCalendar()
    sessions = calendar.sessions(
        datetime(2026, 1, 5, tzinfo=MARKET_TZ),
        datetime(2026, 1, 5, 23, tzinfo=MARKET_TZ),
    )
    assert len(sessions) == 1
    start = datetime.fromtimestamp(sessions[0].start_ns / 1_000_000_000, tz=timezone.utc).astimezone(MARKET_TZ)
    end = datetime.fromtimestamp(sessions[0].end_ns / 1_000_000_000, tz=timezone.utc).astimezone(MARKET_TZ)
    assert start.time().isoformat() == "09:15:00"
    assert end.time().isoformat() == "15:29:00"
