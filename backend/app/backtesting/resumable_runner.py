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
    """Process ordered events after the persisted checkpoint and persist progress."""
    if not run_id.strip():
        raise ValueError("run_id is required")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    checkpoint = ledger.checkpoint(run_id)
    start_cursor = checkpoint["cursor"] if checkpoint else None
    pending = []
    skipped = start_cursor is None
    for record in events:
        cursor = _cursor(record)
        if not skipped:
            if cursor == start_cursor:
                skipped = True
            continue
        pending.append(record)
        if len(pending) >= chunk_size:
            _persist_chunk(engine, pending, strategy, ledger, run_id, price_field)
            pending = []
    if pending:
        _persist_chunk(engine, pending, strategy, ledger, run_id, price_field)

    result = engine.run_events(events, strategy, price_field=price_field)
    if start_cursor is None:
        ledger.save_checkpoint(run_id, _cursor(result.trades[-1]) if result.trades else "complete:0", ledger.count(run_id))
    return result


def _persist_chunk(engine, records, strategy, ledger, run_id, price_field) -> None:
    result = engine.run_events(records, strategy, price_field=price_field)
    if result.trades:
        ledger.append_next(run_id, result.trades)
    ledger.save_checkpoint(run_id, _cursor(records[-1]), ledger.count(run_id))


def _cursor(record: HistoricalRecord) -> str:
    return f"{record.timestamp_ns}:{record.sequence if record.sequence is not None else -1}"
