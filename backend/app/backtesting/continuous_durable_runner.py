"""Helpers for durable continuous-futures backtest execution."""

from __future__ import annotations

from typing import Iterable, Mapping

from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.continuous_futures import ContinuousFuturesRecord
from app.backtesting.engine import BacktestEngine, BacktestResult, EventStrategy
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalRecord


def run_continuous_futures_events_to_durable_ledger(
    engine: BacktestEngine,
    windows: Iterable[FNORolloverWindow],
    records_by_token: Mapping[str, Iterable[HistoricalRecord]],
    strategy: EventStrategy,
    *,
    ledger: BacktestTradeLedger,
    run_id: str,
    price_field: str = "close",
    chunk_size: int = 500,
) -> BacktestResult:
    """Run continuous-futures events and persist both trades and run summary."""
    if not run_id.strip():
        raise ValueError("run_id is required")

    result = engine.run_continuous_futures_events_to_ledger(
        windows,
        records_by_token,
        strategy,
        ledger=ledger,
        run_id=run_id,
        price_field=price_field,
        chunk_size=chunk_size,
    )
    ledger.save_run(run_id, result)
    return result


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
