"""Adapter contract for genuine historical tick/second-resolution data.

Angel One's historical candle API starts at one minute, so sub-minute data
must enter the catalog through a provider that actually supplies those
observations. This adapter keeps that provider boundary explicit and preserves
source timestamps without resampling or synthesis.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .historical_catalog import HistoricalRecord
from .historical_ingest import HistoricalFetchRequest


class GenuineTickHistoricalSource:
    """Wrap a real tick/second provider as a HistoricalSource.

    ``fetcher`` must return source-backed observations. It is intentionally
    injected so a concrete vendor (or an imported historical tick file) can be
    added without changing the durable ingestion layer.
    """

    source_name = "genuine_tick"
    SUPPORTED_TIMEFRAMES = frozenset({"ms", "s"})

    def __init__(
        self,
        fetcher: Callable[[HistoricalFetchRequest], Iterable[HistoricalRecord]],
        *,
        source_name: str = source_name,
    ) -> None:
        if not source_name.strip():
            raise ValueError("source_name is required")
        self.fetcher = fetcher
        self.source_name = source_name

    def fetch(self, request: HistoricalFetchRequest) -> Iterable[HistoricalRecord]:
        if request.timeframe not in self.SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"genuine tick source requires one of {sorted(self.SUPPORTED_TIMEFRAMES)}; "
                f"got {request.timeframe}"
            )
        for record in self.fetcher(request):
            if record.source != self.source_name:
                raise ValueError("tick provider returned a record with the wrong source")
            if record.instrument != request.instrument or record.timeframe != request.timeframe:
                raise ValueError("tick provider returned a record outside the requested identity")
            if not request.start_ns <= record.timestamp_ns <= request.end_ns:
                raise ValueError("tick provider returned a record outside the requested range")
            if not isinstance(record.timestamp_ns, int) or record.timestamp_ns < 0:
                raise ValueError("tick provider must preserve a non-negative integer timestamp_ns")
            yield record

    def from_rows(
        self,
        request: HistoricalFetchRequest,
        rows: Iterable[dict[str, Any]],
    ) -> Iterable[HistoricalRecord]:
        """Convert already acquired raw rows without changing their timestamps."""
        for row in rows:
            if "timestamp_ns" not in row:
                raise ValueError("raw tick row must contain timestamp_ns")
            payload = dict(row)
            timestamp_ns = payload.pop("timestamp_ns")
            yield HistoricalRecord(
                source=self.source_name,
                instrument=request.instrument,
                timeframe=request.timeframe,
                timestamp_ns=timestamp_ns,
                payload=payload,
                sequence=payload.pop("sequence", None),
            )
