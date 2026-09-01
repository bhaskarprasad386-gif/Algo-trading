from datetime import date, datetime

from app.market_data.market_session import should_start_live_data


def test_startup_flow_blocks_live_data_on_weekend():
    assert should_start_live_data(datetime(2026, 9, 5, 10, 0)) is False


def test_startup_flow_blocks_live_data_on_exchange_holiday():
    assert should_start_live_data(
        datetime(2026, 9, 7, 10, 0),
        holidays={date(2026, 9, 7)},
    ) is False


def test_startup_flow_allows_live_data_only_inside_session():
    assert should_start_live_data(datetime(2026, 9, 7, 10, 0)) is True
    assert should_start_live_data(datetime(2026, 9, 7, 15, 30)) is False
