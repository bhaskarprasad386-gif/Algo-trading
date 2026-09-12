from datetime import datetime
import json

import pytest

from app.backtesting.cash_future_strategy_checkpoint import (
    CashFutureStrategyCheckpoint,
    validate_cash_future_checkpoint,
)


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
        strategy_state={"lookback": 20, "last_signal": "BUY"},
    )


def test_checkpoint_round_trips_as_json():
    original = checkpoint()
    restored = CashFutureStrategyCheckpoint.from_json(original.to_json())
    assert restored == original


def test_checkpoint_json_is_deterministic():
    original = checkpoint()
    assert original.to_json() == original.to_json()


def test_checkpoint_rejects_missing_state():
    data = json.loads(checkpoint().to_json())
    del data["reserved_margin"]
    with pytest.raises(ValueError, match="reserved_margin"):
        CashFutureStrategyCheckpoint.from_mapping(data)


def test_checkpoint_rejects_invalid_timestamp():
    data = checkpoint().__dict__.copy()
    data["last_timestamp"] = "not-a-timestamp"
    with pytest.raises(ValueError, match="last_timestamp"):
        CashFutureStrategyCheckpoint.from_mapping(data)


def test_checkpoint_rejects_malformed_json():
    with pytest.raises(ValueError, match="valid JSON"):
        CashFutureStrategyCheckpoint.from_json("{not-json")


@pytest.mark.parametrize("field", ["realized_capital", "reserved_margin"])
def test_checkpoint_rejects_non_finite_numeric_state(field):
    data = checkpoint().__dict__.copy()
    data[field] = float("nan")
    with pytest.raises(ValueError, match="finite number"):
        CashFutureStrategyCheckpoint.from_mapping(data)


def test_checkpoint_rejects_negative_blocked_entries():
    data = checkpoint().__dict__.copy()
    data["blocked_entries"] = -1
    with pytest.raises(ValueError, match="cannot be negative"):
        CashFutureStrategyCheckpoint.from_mapping(data)


def test_checkpoint_rejects_invalid_open_entry():
    data = checkpoint().__dict__.copy()
    data["open_entry"] = ["not", "an", "object"]
    with pytest.raises(ValueError, match="open_entry"):
        CashFutureStrategyCheckpoint.from_mapping(data)


def test_checkpoint_rejects_strategy_state_that_is_not_json_safe():
    data = checkpoint().__dict__.copy()
    data["strategy_state"] = {"bad": object()}
    with pytest.raises(ValueError, match="JSON-serializable"):
        CashFutureStrategyCheckpoint.from_mapping(data)


def test_checkpoint_rejects_strategy_state_with_non_finite_number():
    data = checkpoint().__dict__.copy()
    data["strategy_state"] = {"bad": float("inf")}
    with pytest.raises(ValueError, match="JSON-serializable"):
        CashFutureStrategyCheckpoint.from_mapping(data)


def test_resume_validation_accepts_matching_identity():
    validate_cash_future_checkpoint(
        checkpoint(),
        run_id="run-1",
        strategy_id="mean-reversion",
        strategy_version="3",
        strategy_hash="abc123",
        data_source_fingerprint="source-1",
    )


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("run_id", "other-run", "run_id mismatch"),
        ("strategy_id", "other-strategy", "strategy_id mismatch"),
        ("strategy_version", "4", "strategy_version mismatch"),
        ("strategy_hash", "different", "strategy_hash mismatch"),
        ("data_source_fingerprint", "different", "source_fingerprint mismatch"),
    ],
)
def test_resume_validation_rejects_identity_mismatch(field, value, message):
    kwargs = {
        "run_id": "run-1",
        "strategy_id": "mean-reversion",
        "strategy_version": "3",
        "strategy_hash": "abc123",
        "data_source_fingerprint": "source-1",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        validate_cash_future_checkpoint(checkpoint(), **kwargs)
