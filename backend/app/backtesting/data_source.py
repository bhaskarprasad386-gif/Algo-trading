"""Data-source implementations for the universal backtest core."""

from __future__ import annotations

from typing import Iterable

from app.backtesting.contracts import DataSourceProtocol
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


class CatalogDataSource:
    """Streaming DataSource backed by HistoricalCatalog.

    The selected range is never materialized before iteration.
    """

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
