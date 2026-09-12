from datetime import datetime

import pytest

from app.backtesting.cash_future_checkpoint import CashFutureCheckpointState


def test_checkpoint_round_trip_is_json_safe():
    state = CashFutureCheckpointState(
        event_index=7,
        timestamp=datetime(2026, 9, 11, 10, 15),
        selected_contract="SEP",
        realized_capital=10000570.0,
        reserved_margin=10000.0,
        blocked_entries=2,
        entry={"symbol": "SBIN", "lot_size": 750},
        strategy_id="gap-test",
        strategy_version="1",
        strategy_hash="abc",
        data_source_fingerprint="source-1",
    )
    restored = CashFutureCheckpointState.from_mapping(state.to_mapping())
    assert restored == state


def test_checkpoint_rejects_missing_required_state():
    with pytest.raises(ValueError, match="missing fields"):
        CashFutureCheckpointState.from_mapping({"event_index": 1})


def test_checkpoint_rejects_invalid_capital_or_margin():
    with pytest.raises(ValueError, match="realized_capital"):
        CashFutureCheckpointState(
            0, datetime(2026, 9, 11), None, 0, 0, 0, None, "s", "1"
        )
    with pytest.raises(ValueError, match="reserved_margin"):
        CashFutureCheckpointState(
            0, datetime(2026, 9, 11), None, 100.0, -1, 0, None, "s", "1"
        )
