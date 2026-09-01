from datetime import date, datetime

from app.market_data.market_session import MarketSession, should_start_live_data


def test_market_open_window_allows_live_data():
    session = MarketSession()
    assert session.is_open(datetime(2026, 9, 1, 10, 0)) is True


def test_before_open_and_after_close_block_live_data():
    session = MarketSession()
    assert session.is_open(datetime(2026, 9, 1, 9, 14, 59)) is False
    assert session.is_open(datetime(2026, 9, 1, 15, 30)) is False


def test_weekend_and_exchange_holiday_block_live_data():
    holiday = date(2026, 9, 2)
    session = MarketSession(holidays=frozenset({holiday}))
    assert session.is_open(datetime(2026, 9, 5, 10, 0)) is False
    assert session.is_open(datetime(2026, 9, 2, 10, 0)) is False


def test_should_start_live_data_uses_exchange_holiday_calendar():
    assert should_start_live_data(
        datetime(2026, 9, 2, 10, 0),
        holidays={date(2026, 9, 2)},
    ) is False
