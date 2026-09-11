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
    """Run only the unprocessed event suffix and persist its progress."""
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
        return engine.run_events((), strategy, price_field=price_field)

    all_trades = []
    final_result = None
    for offset in range(0, len(suffix), chunk_size):
        chunk = suffix[offset:offset + chunk_size]
        final_result = engine.run_events(chunk, strategy, price_field=price_field)
        if final_result.trades:
            ledger.append_next(run_id, final_result.trades)
            all_trades.extend(final_result.trades)
        ledger.save_checkpoint(run_id, _cursor(chunk[-1]), ledger.count(run_id))

    if final_result is None:
        return engine.run_events((), strategy, price_field=price_field)

    return BacktestResult(
        initial_capital=engine.config.initial_capital,
        final_capital=engine.config.initial_capital + ledger.net_pnl(run_id),
        net_pnl=ledger.net_pnl(run_id),
        total_return=ledger.net_pnl(run_id) / engine.config.initial_capital,
        trades=tuple(all_trades),
        win_rate=(sum(1 for trade in all_trades if trade.net_pnl > 0) / len(all_trades)) if all_trades else 0.0,
        expectancy=(sum(trade.net_pnl for trade in all_trades) / len(all_trades)) if all_trades else 0.0,
        sharpe_ratio=final_result.sharpe_ratio,
        sortino_ratio=final_result.sortino_ratio,
        max_drawdown=final_result.max_drawdown,
        cagr=final_result.cagr,
    )


def _cursor(record: HistoricalRecord) -> str:
    return f"{record.timestamp_ns}:{record.sequence if record.sequence is not None else -1}"
