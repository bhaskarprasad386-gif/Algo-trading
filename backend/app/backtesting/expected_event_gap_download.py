"""Bounded repair planning for irregular historical events with explicit expectations."""

from __future__ import annotations

from dataclasses import dataclass

from .historical_expected_events import missing_expected_timestamps
from .historical_ingest import HistoricalFetchRequest
from .historical_sync import HistoricalSyncPlan


@dataclass(frozen=True)
class ExpectedEventGapDownloadPlanner:
    """Turn an authoritative expected-event timestamp set into bounded requests.

    Unlike candle gap planning, this never assumes a fixed cadence. The caller must
    supply the timestamps that are actually expected for the requested dataset.
    """

    max_request_ns: int

    def __post_init__(self) -> None:
        if self.max_request_ns <= 0:
            raise ValueError("max_request_ns must be positive")

    @staticmethod
    def _split_timestamp_runs(
        timestamps: tuple[int, ...], max_request_ns: int
    ) -> tuple[tuple[int, int], ...]:
        if not timestamps:
            return ()
        ranges: list[tuple[int, int]] = []
        start = previous = timestamps[0]
        for timestamp in timestamps[1:]:
            if timestamp - start <= max_request_ns:
                previous = timestamp
                continue
            ranges.append((start, previous))
            start = previous = timestamp
        ranges.append((start, previous))
        return tuple(ranges)

    def plan(
        self,
        *,
        request: HistoricalFetchRequest,
        expected_timestamps: tuple[int, ...] | list[int],
        catalog,
    ) -> HistoricalSyncPlan:
        """Return bounded requests only for explicitly expected missing events."""
        expected = tuple(
            timestamp
            for timestamp in sorted(set(expected_timestamps))
            if request.start_ns <= timestamp <= request.end_ns
        )
        missing = missing_expected_timestamps(
            catalog,
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            expected_timestamps=expected,
        )
        requests = tuple(
            HistoricalFetchRequest(request.source, request.instrument, request.timeframe, start, end)
            for start, end in self._split_timestamp_runs(missing, self.max_request_ns)
        )
        return HistoricalSyncPlan(requests)


__all__ = ["ExpectedEventGapDownloadPlanner"]
