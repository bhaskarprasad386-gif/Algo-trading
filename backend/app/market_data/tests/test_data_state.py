from datetime import datetime

from app.market_data.data_state import DataState


def test_data_state_requires_history_and_open_market_for_live():
    state = DataState(historical_complete=True, live_running=False)
    assert state.ready_for_live(True) is True
    assert state.ready_for_live(False) is False


def test_incomplete_history_never_allows_live():
    state = DataState(historical_complete=False, live_running=False)
    assert state.ready_for_live(True) is False


def test_update_timestamp_is_optional():
    timestamp = datetime(2026, 9, 1, 10, 0)
    state = DataState(historical_complete=True, live_running=True, updated_at=timestamp)
    assert state.updated_at == timestamp
