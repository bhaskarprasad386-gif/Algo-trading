from __future__ import annotations

from collections.abc import Callable, Iterable

from .backtesting import BacktestConfig, BacktestResult, run_events_incremental
from .historical_catalog import HistoricalCatalog


# Existing public API types are kept local to this module so callers can stream
# catalog records without materializing a complete event list.
EventStrategy = Callable[[object], str | None]
PersistTradeChunk = Callable[[object, int], None]


def run_catalog_events_incremental(
    catalog: HistoricalCatalog,
    engine_config: BacktestConfig,
    *,
    source: str,
    instrument: str,
    strategy: EventStrategy,
    persist_chunk: PersistTradeChunk,
    timeframe: str = "tick",
    start_ns: int | None = None,
    end_ns: int | None = None,
    chunk_size: int = 500,
    price_field: str = "price",
) -> BacktestResult:
    """Stream catalog records into bounded-chunk event backtesting.

    HistoricalCatalog exposes the streaming ``iter_records`` API; using it
    keeps the runner compatible with range scans and avoids event-list
    materialization for large backtests.
    """
    return run_events_incremental(
        engine_config,
        catalog.iter_records(
            source=source,
            instrument=instrument,
            timeframe=timeframe,
            start_ns=start_ns,
            end_ns=end_ns,
        ),
        strategy,
        persist_chunk=persist_chunk,
        chunk_size=chunk_size,
        price_field=price_field,
    )
