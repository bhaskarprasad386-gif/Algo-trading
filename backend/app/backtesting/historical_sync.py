"""Durable, bounded historical sync orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .historical_ingest import HistoricalFetchRequest, HistoricalIngestionService, HistoricalSource, HistoricalSyncResult


@dataclass(frozen=True)
class HistoricalSyncPlan:
    requests: tuple[HistoricalFetchRequest, ...]


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


class HistoricalSyncRunner:
    """Execute one bounded request at a time; each completed chunk is durable."""

    def __init__(self, service: HistoricalIngestionService) -> None:
        self.service = service

    def run(self, source: HistoricalSource, plan: HistoricalSyncPlan) -> tuple[HistoricalSyncResult, ...]:
        results: list[HistoricalSyncResult] = []
        for request in plan.requests:
            results.append(self.service.sync(source, request))
        return tuple(results)
