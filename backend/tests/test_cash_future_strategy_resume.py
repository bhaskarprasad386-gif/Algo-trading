from datetime import datetime

import pytest

from app.backtesting.cash_future_strategy_checkpoint import CashFutureStrategyCheckpoint
from app.backtesting.cash_future_strategy_resume import load_validated_cash_future_checkpoint
from app.backtesting.ledger import BacktestLedger, Checkpoint


def _checkpoint() -> CashFutureStrategyCheckpoint:
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


def _ledger() -> BacktestLedger:
    ledger = BacktestLedger(":memory:")
    ledger.start_run(
        "run-1",
        "mean-reversion",
        "3",
        100000000.0,
        strategy_hash="abc123",
        data_source_fingerprint="source-1",
    )
    checkpoint = _checkpoint()
    ledger.checkpoint(
        Checkpoint(
            run_id="run-1",
            event_index=10,
            timestamp_ns=checkpoint.last_timestamp and 1000,
            state={"run_id": "run-1", **checkpoint.__dict__},
        )
    )
    return ledger


def test_load_validated_checkpoint_returns_latest_state():
    restored = load_validated_cash_future_checkpoint(
        _ledger(),
        run_id="run-1",
        strategy_id="mean-reversion",
        strategy_version="3",
        strategy_hash="abc123",
        data_source_fingerprint="source-1",
    )
    assert restored.selected_contract == "SEP"
    assert restored.reserved_margin == 25000.0


@pytest.mark.parametrize(
    ("strategy_id", "strategy_version", "strategy_hash", "fingerprint"),
    [
        ("other", "3", "abc123", "source-1"),
        ("mean-reversion", "4", "abc123", "source-1"),
        ("mean-reversion", "3", "changed", "source-1"),
        ("mean-reversion", "3", "abc123", "changed"),
    ],
)
def test_load_validated_checkpoint_rejects_identity_mismatch(
    strategy_id, strategy_version, strategy_hash, fingerprint
):
    with pytest.raises(ValueError, match="unsafe Cash-Future resume"):
        load_validated_cash_future_checkpoint(
            _ledger(),
            run_id="run-1",
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_hash=strategy_hash,
            data_source_fingerprint=fingerprint,
        )


def test_load_validated_checkpoint_requires_checkpoint():
    ledger = BacktestLedger(":memory:")
    ledger.start_run(
        "run-empty",
        "mean-reversion",
        "3",
        100000000.0,
        data_source_fingerprint="source-1",
    )
    with pytest.raises(ValueError, match="no Cash-Future checkpoint"):
        load_validated_cash_future_checkpoint(
            ledger,
            run_id="run-empty",
            strategy_id="mean-reversion",
            strategy_version="3",
            strategy_hash=None,
            data_source_fingerprint="source-1",
        )
