"""Durable runner for continuous-futures event backtests."""

from __future__ import annotations

from typing import Iterable, Mapping

from app.backtesting.backtest_ledger import BacktestTradeLedger
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
    """Replay continuous futures and durably persist trades plus the final summary."""
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


__all__ = ["run_continuous_futures_events_to_durable_ledger"]
