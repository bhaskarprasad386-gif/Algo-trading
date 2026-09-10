"""Durable, bounded historical sync orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .historical_expected_events import build_expected_event_repair_plan
from .historical_ingest import HistoricalFetchRequest, HistoricalIngestionService, HistoricalSource, HistoricalSyncResult


@dataclass(frozen=True)
class HistoricalSyncPlan:
    requests: tuple[HistoricalFetchRequest, ...]


@dataclass(frozen=True)
class HistoricalStreamingResult:
    """Scalar-only outcome for large plans; raw chunk results are optional."""

    results: tuple[HistoricalSyncResult, ...]
    completed_chunks: int
    failed_request_index: int | None = None


def build_chunked_plan(*, source: str, instrument: str, timeframe: str, start_ns: int, end_ns: int, chunk_ns: int) -> HistoricalSyncPlan:
    """Build inclusive, non-overlapping chunks covering the requested range."""
    if chunk_ns <= 0:
        raise ValueError("chunk_ns must be positive")
    if start_ns < 0 or end_ns < start_ns:
        raise ValueError("invalid historical range")
    requests: list[HistoricalFetchRequest] = []
    cursor = start_ns
    while cursor <= end_ns:
        chunk_end = min(end_ns, cursor + chunk_ns - 1)
        requests.append(HistoricalFetchRequest(source, instrument, timeframe, cursor, chunk_end))
        if chunk_end == end_ns:
            break
        cursor = chunk_end + 1
    return HistoricalSyncPlan(tuple(requests))


def build_session_gap_plan(
    catalog,
    calendar,
    *,
    source: str,
    instrument: str,
    timeframe: str,
    interval_ns: int,
    start_date,
    end_date,
    max_request_ns: int,
) -> HistoricalSyncPlan:
    """Build bounded repair requests for cadence gaps inside trading sessions only.

    This planner is for fixed-cadence bars. Session boundaries, weekends and
    configured closed dates are never bridged. Non-cadenced tick/quote/depth/event
    streams must use ``build_expected_event_plan`` instead.
    """
    if interval_ns <= 0:
        raise ValueError("interval_ns must be positive")
    if max_request_ns <= 0:
        raise ValueError("max_request_ns must be positive")

    gaps = catalog.session_gaps(
        source=source,
        instrument=instrument,
        timeframe=timeframe,
        interval_ns=interval_ns,
        calendar=calendar,
        start_date=start_date,
        end_date=end_date,
    )
    requests: list[HistoricalFetchRequest] = []
    for gap in gaps:
        requests.extend(
            build_chunked_plan(
                source=source,
                instrument=instrument,
                timeframe=timeframe,
                start_ns=gap.start_ns,
                end_ns=gap.end_ns,
                chunk_ns=max_request_ns,
            ).requests
        )
    return HistoricalSyncPlan(tuple(requests))


def build_expected_event_plan(
    catalog,
    *,
    source: str,
    instrument: str,
    timeframe: str,
    expected_timestamps,
    max_request_ns: int,
) -> HistoricalSyncPlan:
    """Build a bounded sync plan from authoritative non-cadenced event timestamps.

    This is deliberately separate from ``build_chunked_plan``: tick/quote/depth/event
    streams must not be treated as fixed-cadence candles. Only timestamps supplied by
    an authoritative expected-event source are considered missing.
    """
    return HistoricalSyncPlan(tuple(build_expected_event_repair_plan(
        catalog,
        source=source,
        instrument=instrument,
        timeframe=timeframe,
        expected_timestamps=expected_timestamps,
        max_request_ns=max_request_ns,
    )))


class HistoricalSyncRunner:
    """Execute one bounded request at a time; each completed chunk is durable."""

    def __init__(self, service: HistoricalIngestionService) -> None:
        self.service = service

    def run(self, source: HistoricalSource, plan: HistoricalSyncPlan) -> tuple[HistoricalSyncResult, ...]:
        results: list[HistoricalSyncResult] = []
        for request in plan.requests:
            results.append(self.service.sync(source, request))
        return tuple(results)

    def run_streaming(
        self,
        source: HistoricalSource,
        plan: HistoricalSyncPlan,
        *,
        collect_results: bool = False,
        batch_size: int = 1024,
        on_batch: Callable[[int, int], None] | None = None,
        on_chunk_complete: Callable[[int, HistoricalSyncResult], None] | None = None,
    ) -> HistoricalStreamingResult:
        """Run a large plan without retaining all chunk results in memory."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        results: list[HistoricalSyncResult] = []
        completed = 0
        for index, request in enumerate(plan.requests):
            result = self.service.sync_streaming(source, request, batch_size=batch_size, on_batch=on_batch)
            completed += 1
            if collect_results:
                results.append(result)
            if on_chunk_complete is not None:
                on_chunk_complete(index, result)
        return HistoricalStreamingResult(tuple(results), completed, None)
