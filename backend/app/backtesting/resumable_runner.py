"""Restart-safe incremental event backtest runner."""

from __future__ import annotations

from collections.abc import Iterable

from app.backtesting.engine import BacktestEngine, BacktestResult, EventStrategy
from app.backtesting.historical_catalog import HistoricalRecord


def run_resumable_events(
    engine: BacktestEngine,
    events: Iterable[HistoricalRecord],
    strategy: EventStrategy,
    *,
    ledger,
    run_id: str,
    price_field: str = "price",
    chunk_size: int = 500,
) -> BacktestResult:
    """Run an event suffix with checkpointed progress."""
    if not run_id.strip():
        raise ValueError("run_id is required")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    materialized = tuple(events)
    checkpoint = ledger.checkpoint(run_id)
    start_cursor = checkpoint["cursor"] if checkpoint else None
    start_index = 0
    if start_cursor is not None:
        for index, record in enumerate(materialized):
            if _cursor(record) == start_cursor:
                start_index = index + 1
                break
        else:
            raise ValueError("persisted checkpoint cursor was not found in event stream")

    suffix = materialized[start_index:]
    if not suffix:
        return _empty_result(engine, ledger, run_id)

    # The same strategy instance is used for every chunk, so stateful strategy
    # objects can carry open-position state across chunk boundaries.
    all_trades = []
    last_result = None
    for offset in range(0, len(suffix), chunk_size):
        chunk = suffix[offset:offset + chunk_size]
        result = engine.run_events(chunk, strategy, price_field=price_field)
        last_result = result
        if result.trades:
            ledger.append_next(run_id, result.trades)
            all_trades.extend(result.trades)
        ledger.save_checkpoint(run_id, _cursor(chunk[-1]), ledger.count(run_id))

    return _result_from_ledger(engine, ledger, run_id, all_trades, last_result)


def _empty_result(engine, ledger, run_id):
    pnl = ledger.net_pnl(run_id)
    initial = engine.config.initial_capital
    return BacktestResult(initial, initial + pnl, pnl, pnl / initial, (), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _result_from_ledger(engine, ledger, run_id, trades, last_result):
    pnl = ledger.net_pnl(run_id)
    initial = engine.config.initial_capital
    return BacktestResult(
        initial, initial + pnl, pnl, pnl / initial, tuple(trades),
        sum(1 for trade in trades if trade.net_pnl > 0) / len(trades) if trades else 0.0,
        pnl / len(trades) if trades else 0.0,
        last_result.sharpe_ratio if last_result else 0.0,
        last_result.sortino_ratio if last_result else 0.0,
        last_result.max_drawdown if last_result else 0.0,
        last_result.cagr if last_result else 0.0,
    )


def _cursor(record: HistoricalRecord) -> str:
    return f"{record.timestamp_ns}:{record.sequence if record.sequence is not None else -1}"
