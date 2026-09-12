from datetime import datetime

from app.backtesting.cash_future_strategy_checkpoint import CashFutureStrategyCheckpoint


def checkpoint() -> CashFutureStrategyCheckpoint:
    return CashFutureStrategyCheckpoint(
        run_id="run-1",
        strategy_id="mean-reversion",
        strategy_version="3",
        strategy_hash="abc123",
        last_timestamp=datetime(2026, 9, 12, 10, 15).isoformat(),
        selected_contract="SEP",
        realized_capital=10000500.0,
        reserved_margin=25000.0,
        blocked_entries=2,
        open_entry={"symbol": "SBIN", "lot_size": 750},
        source_fingerprint="source-1",
    )


def test_checkpoint_round_trips_as_json():
    original = checkpoint()
    restored = CashFutureStrategyCheckpoint.from_json(original.to_json())
    assert restored == original


def test_checkpoint_json_is_deterministic():
    original = checkpoint()
    assert original.to_json() == original.to_json()


def test_checkpoint_rejects_missing_state():
    payload = checkpoint().to_json()
    import json

    data = json.loads(payload)
    del data["reserved_margin"]
    try:
        CashFutureStrategyCheckpoint.from_mapping(data)
    except ValueError as exc:
        assert "reserved_margin" in str(exc)
    else:
        raise AssertionError("incomplete checkpoint was accepted")


def test_checkpoint_rejects_invalid_timestamp():
    data = checkpoint().__dict__.copy()
    data["last_timestamp"] = "not-a-timestamp"
    try:
        CashFutureStrategyCheckpoint.from_mapping(data)
    except ValueError as exc:
        assert "last_timestamp" in str(exc)
    else:
        raise AssertionError("invalid timestamp was accepted")
