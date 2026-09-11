"""Helpers for completing backtests with durable trade and run persistence."""

from __future__ import annotations

from typing import Iterable, Mapping

from app.algo.strategy import Strategy
from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.engine import BacktestEngine, BacktestResult


def run_incremental_to_durable_ledger(
    engine: BacktestEngine,
    candles: Iterable[Mapping[str, object]],
    entry_strategy: Strategy,
    exit_strategy: Strategy,
    *,
    ledger: BacktestTradeLedger,
    run_id: str,
    chunk_size: int = 500,
) -> BacktestResult:
    """Run a backtest and durably persist both trades and its final run summary."""
    if not run_id.strip():
        raise ValueError("run_id is required")

    result = engine.run_incremental_to_ledger(
        candles,
        entry_strategy,
        exit_strategy,
        ledger=ledger,
        run_id=run_id,
        chunk_size=chunk_size,
    )
    ledger.save_run(run_id, result)
    return result
