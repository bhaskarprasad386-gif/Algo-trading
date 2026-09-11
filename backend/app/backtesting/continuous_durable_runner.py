"""Helpers for persisting continuous-futures backtests and run summaries."""

from __future__ import annotations

from typing import Iterable

from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.continuous_futures import ContinuousFuturesRecord
from app.backtesting.engine import BacktestResult


def save_continuous_backtest_run(
    ledger: BacktestTradeLedger,
    run_id: str,
    result: BacktestResult,
) -> BacktestResult:
    """Persist the completed continuous-futures run summary."""
    if not run_id.strip():
        raise ValueError("run_id is required")
    ledger.save_run(run_id, result)
    return result
