"""Provider-agnostic incremental historical-data ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .historical_catalog import Gap, HistoricalCatalog, HistoricalRecord


@dataclass(frozen=True)
class HistoricalFetchRequest:
    source: str
    instrument: str
    timeframe: str
    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.instrument.strip() or not self.timeframe.strip():
            raise ValueError("source, instrument and timeframe are required")
        if self.start_ns < 0 or self.end_ns < self.start_ns:
            raise ValueError("invalid historical fetch range")


class HistoricalSource(Protocol):
    """A real provider adapter; implementations must return source-backed records only."""

    def fetch(self, request: HistoricalFetchRequest) -> Iterable[HistoricalRecord]:
        ...


@dataclass(frozen=True)
class HistoricalSyncResult:
    requested: HistoricalFetchRequest
    inserted: int
    fetched: int
    final_watermark_ns: int | None


class HistoricalIngestionService:
    """Incrementally fetches and merges data through the durable catalog.

    Session/calendar rules are intentionally outside this layer. A caller may
    provide explicit repair ranges so overnight/weekend gaps are never treated
    as missing market bars merely because timestamps are discontinuous.
    """

    def __init__(self, catalog: HistoricalCatalog) -> None:
        self.catalog = catalog

    def next_request(
        self,
        *,
        source: str,
        instrument: str,
        timeframe: str,
        end_ns: int,
        interval_ns: int,
        start_ns: int | None = None,
    ) -> HistoricalFetchRequest:
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        if end_ns < 0:
            raise ValueError("end_ns cannot be negative")
        watermark = self.catalog.watermark(
            source=source, instrument=instrument, timeframe=timeframe
        )
        if start_ns is None:
            start_ns = 0 if watermark is None else watermark + interval_ns
        elif start_ns < 0:
            raise ValueError("start_ns cannot be negative")
        return HistoricalFetchRequest(source, instrument, timeframe, start_ns, end_ns)

    def sync(
        self,
        source_adapter: HistoricalSource,
        request: HistoricalFetchRequest,
        *,
        ingested_at_ns: int = 0,
    ) -> HistoricalSyncResult:
        records = tuple(source_adapter.fetch(request))
        for record in records:
            if record.source != request.source or record.instrument != request.instrument or record.timeframe != request.timeframe:
                raise ValueError("source adapter returned a record outside the requested identity")
            if not request.start_ns <= record.timestamp_ns <= request.end_ns:
                raise ValueError("source adapter returned a record outside the requested range")
        inserted = self.catalog.ingest(records, ingested_at_ns=ingested_at_ns)
        return HistoricalSyncResult(
            requested=request,
            inserted=inserted,
            fetched=len(records),
            final_watermark_ns=self.catalog.watermark(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
            ),
        )

    def repair_ranges(
        self,
        *,
        source: str,
        instrument: str,
        timeframe: str,
        interval_ns: int,
    ) -> tuple[Gap, ...]:
        """Expose candidate cadence gaps for a session-aware repair planner."""
        return self.catalog.gaps(
            source=source,
            instrument=instrument,
            timeframe=timeframe,
            interval_ns=interval_ns,
        )
