"""Provider-agnostic incremental historical-data ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

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

    @staticmethod
    def _validate_record(record: HistoricalRecord, request: HistoricalFetchRequest) -> None:
        if (
            record.source != request.source
            or record.instrument != request.instrument
            or record.timeframe != request.timeframe
        ):
            raise ValueError("source adapter returned a record outside the requested identity")
        if not request.start_ns <= record.timestamp_ns <= request.end_ns:
            raise ValueError("source adapter returned a record outside the requested range")

    @staticmethod
    def _validate_capabilities(source_adapter: HistoricalSource, request: HistoricalFetchRequest) -> None:
        """Honor optional provider capability declarations without breaking legacy adapters."""
        provider_capabilities = getattr(source_adapter, "capabilities", None)
        if provider_capabilities is not None:
            require = getattr(provider_capabilities, "require", None)
            if require is None or not callable(require):
                raise TypeError("source adapter capabilities must provide require(timeframe)")
            require(request.timeframe)

    def sync(
        self,
        source_adapter: HistoricalSource,
        request: HistoricalFetchRequest,
        *,
        ingested_at_ns: int = 0,
    ) -> HistoricalSyncResult:
        return self.sync_streaming(
            source_adapter,
            request,
            ingested_at_ns=ingested_at_ns,
            batch_size=1024,
        )

    def sync_streaming(
        self,
        source_adapter: HistoricalSource,
        request: HistoricalFetchRequest,
        *,
        ingested_at_ns: int = 0,
        batch_size: int = 1024,
        on_batch: Callable[[int, int], None] | None = None,
    ) -> HistoricalSyncResult:
        """Consume provider records in bounded batches without retaining the full range.

        ``on_batch(inserted_total, fetched_total)`` is optional and receives only
        scalar progress, never raw records. Each accepted batch is durably
        committed by ``HistoricalCatalog.ingest`` before the next batch is read.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if ingested_at_ns < 0:
            raise ValueError("ingested_at_ns cannot be negative")

        self._validate_capabilities(source_adapter, request)

        inserted_total = 0
        fetched_total = 0
        batch: list[HistoricalRecord] = []
        for record in source_adapter.fetch(request):
            self._validate_record(record, request)
            batch.append(record)
            if len(batch) < batch_size:
                continue
            inserted_total += self.catalog.ingest(batch, ingested_at_ns=ingested_at_ns)
            fetched_total += len(batch)
            if on_batch is not None:
                on_batch(inserted_total, fetched_total)
            batch.clear()

        if batch:
            inserted_total += self.catalog.ingest(batch, ingested_at_ns=ingested_at_ns)
            fetched_total += len(batch)
            if on_batch is not None:
                on_batch(inserted_total, fetched_total)

        return HistoricalSyncResult(
            requested=request,
            inserted=inserted_total,
            fetched=fetched_total,
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

    def repair_expected_events(
        self,
        source_adapter: HistoricalSource,
        *,
        source: str,
        instrument: str,
        timeframe: str,
        expected_timestamps: Iterable[int],
        max_request_ns: int,
        ingested_at_ns: int = 0,
        batch_size: int = 1024,
        on_batch: Callable[[int, int], None] | None = None,
    ) -> tuple[HistoricalSyncResult, ...]:
        """Repair only explicitly expected non-cadenced events.

        The expected timestamp set must come from an authoritative source such
        as an exchange/provider event manifest. No fixed event cadence is
        inferred here. Candle/session acquisition should continue to use
        ``repair_ranges`` or its dedicated session-aware planner instead.
        """
        from .historical_expected_events import build_expected_event_repair_plan

        requests = build_expected_event_repair_plan(
            self.catalog,
            source=source,
            instrument=instrument,
            timeframe=timeframe,
            expected_timestamps=expected_timestamps,
            max_request_ns=max_request_ns,
        )
        return tuple(
            self.sync_streaming(
                source_adapter,
                request,
                ingested_at_ns=ingested_at_ns,
                batch_size=batch_size,
                on_batch=on_batch,
            )
            for request in requests
        )
