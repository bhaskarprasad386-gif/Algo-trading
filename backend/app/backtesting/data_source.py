"""Data-source implementations for the universal backtest core."""

from __future__ import annotations

import heapq
from typing import Iterable, Iterator

from app.backtesting.contracts import DataSourceProtocol
from app.backtesting.event_model import event_order_key
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


class CatalogDataSource:
    """Streaming DataSource backed by HistoricalCatalog."""

    def __init__(
        self,
        catalog: HistoricalCatalog,
        *,
        source: str,
        instrument: str | None = None,
        timeframe: str,
    ) -> None:
        if not source.strip():
            raise ValueError("source is required")
        if instrument is not None and not instrument.strip():
            raise ValueError("instrument cannot be blank when provided")
        if not timeframe.strip():
            raise ValueError("timeframe is required")
        self.catalog = catalog
        self.source = source
        self.instrument = instrument
        self.timeframe = timeframe

    def iter_events(
        self,
        *,
        start_ns: int | None = None,
        end_ns: int | None = None,
    ) -> Iterable[HistoricalRecord]:
        return self.catalog.iter_records(
            source=self.source,
            instrument=self.instrument,
            timeframe=self.timeframe,
            start_ns=start_ns,
            end_ns=end_ns,
        )

    def __iter__(self):
        return self.iter_events()


class MultiInstrumentDataSource:
    """Lazily merge ordered streams from multiple instruments.

    Each instrument keeps its SQLite cursor streaming; only one pending event
    per instrument is held in memory while the streams are merged.
    """

    def __init__(
        self,
        catalog: HistoricalCatalog,
        *,
        source: str,
        instruments: Iterable[str],
        timeframe: str,
    ) -> None:
        if not source.strip():
            raise ValueError("source is required")
        if not timeframe.strip():
            raise ValueError("timeframe is required")
        values = tuple(dict.fromkeys(instruments))
        if not values or any(not value.strip() for value in values):
            raise ValueError("instruments must contain at least one non-blank value")
        self.catalog = catalog
        self.source = source
        self.instruments = values
        self.timeframe = timeframe

    def iter_events(
        self,
        *,
        start_ns: int | None = None,
        end_ns: int | None = None,
    ) -> Iterator[HistoricalRecord]:
        streams = [
            iter(
                self.catalog.iter_records(
                    source=self.source,
                    instrument=instrument,
                    timeframe=self.timeframe,
                    start_ns=start_ns,
                    end_ns=end_ns,
                )
            )
            for instrument in self.instruments
        ]
        heap: list[tuple[tuple[int, str, str, str, int], int, HistoricalRecord]] = []
        for index, stream in enumerate(streams):
            try:
                record = next(stream)
            except StopIteration:
                continue
            heapq.heappush(heap, (event_order_key(record), index, record))

        while heap:
            _, index, record = heapq.heappop(heap)
            yield record
            try:
                next_record = next(streams[index])
            except StopIteration:
                continue
            heapq.heappush(heap, (event_order_key(next_record), index, next_record))

    def __iter__(self):
        return self.iter_events()
