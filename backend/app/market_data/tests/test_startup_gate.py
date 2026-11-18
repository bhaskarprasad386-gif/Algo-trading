from datetime import date, datetime

from app.market_data.startup_gate import decide_startup


def test_startup_requires_historical_sync_before_live_data():
    decision = decide_startup(
        datetime(2026, 9, 1, 10, 0),
        historical_data_complete=False,
    )
    assert decision.historical_sync_required is True
    assert decision.live_data_allowed is False


def test_startup_allows_live_only_after_historical_data_is_complete():
    decision = decide_startup(
        datetime(2026, 9, 1, 10, 0),
        historical_data_complete=True,
    )
    assert decision.historical_sync_required is False
    assert decision.live_data_allowed is True


def test_startup_blocks_live_on_holiday_even_when_history_is_complete():
    holiday = date(2026, 9, 1)
    decision = decide_startup(
        datetime(2026, 9, 1, 10, 0),
        historical_data_complete=True,
        holidays={holiday},
    )
    assert decision.live_data_allowed is False
