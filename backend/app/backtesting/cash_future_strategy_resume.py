"""Safe preflight helpers for resumable Cash-Future strategy runs."""

from __future__ import annotations

from app.backtesting.cash_future_strategy_checkpoint import (
    CashFutureStrategyCheckpoint,
    validate_cash_future_checkpoint,
)
from app.backtesting.ledger import BacktestLedger


def load_validated_cash_future_checkpoint(
    ledger: BacktestLedger,
    *,
    run_id: str,
    strategy_id: str,
    strategy_version: str,
    strategy_hash: str | None,
    data_source_fingerprint: str | None,
) -> CashFutureStrategyCheckpoint:
    """Load the latest checkpoint only after validating resume identity.

    This is deliberately a preflight primitive: it does not execute strategy code,
    mutate ledger records, or attempt unsafe restoration of arbitrary strategy state.
    """
    ledger.validate_resume(
        run_id,
        data_source_fingerprint=data_source_fingerprint,
    )
    checkpoint = ledger.load_checkpoint(run_id)
    if checkpoint is None:
        raise ValueError(f"no Cash-Future checkpoint exists for run_id: {run_id}")

    restored = CashFutureStrategyCheckpoint.from_mapping(checkpoint.state)
    validate_cash_future_checkpoint(
        restored,
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=strategy_hash,
        data_source_fingerprint=data_source_fingerprint,
    )
    if checkpoint.event_index <= 0:
        raise ValueError("unsafe Cash-Future resume: checkpoint event_index must be positive")
    if checkpoint.timestamp_ns < 0:
        raise ValueError("unsafe Cash-Future resume: checkpoint timestamp is invalid")
    return restored


__all__ = ["load_validated_cash_future_checkpoint"]
