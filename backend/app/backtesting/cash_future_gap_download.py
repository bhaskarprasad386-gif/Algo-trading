"""Session-aware Cash-Future gap download planning."""

from __future__ import annotations

from dataclasses import dataclass

from .cash_future_coverage_manifest import CoverageRange, build_coverage_manifest
from .cash_future_download_queue import CashFutureDownloadQueue
from .historical_ingest import HistoricalFetchRequest
from .historical_sync import HistoricalSyncPlan
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureGapDownloadPlanner:
    """Convert missing expected timestamps into bounded durable fetch requests."""

    interval_ns: int
    max_request_ns: int

    def __post_init__(self) -> None:
        if self.interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        if self.max_request_ns <= 0:
            raise ValueError("max_request_ns must be positive")
        if self.max_request_ns < self.interval_ns:
            raise ValueError("max_request_ns must be >= interval_ns")

    @staticmethod
    def _expected(sessions: tuple[SessionWindow, ...], request: HistoricalFetchRequest, interval_ns: int) -> tuple[int, ...]:
        timestamps: set[int] = set()
        for session in sessions:
            start = max(session.start_ns, request.start_ns)
            end = min(session.end_ns, request.end_ns)
            if start > end:
                continue
            timestamp = start
            while timestamp <= end:
                timestamps.add(timestamp)
                timestamp += interval_ns
        return tuple(sorted(timestamps))

    @staticmethod
    def _missing_ranges(expected: tuple[int, ...], actual: set[int], interval_ns: int, max_request_ns: int) -> tuple[tuple[int, int], ...]:
        ranges: list[tuple[int, int]] = []
        missing_start: int | None = None
        last_missing: int | None = None
        previous_expected: int | None = None
        for timestamp in expected:
            contiguous = previous_expected is not None and timestamp - previous_expected == interval_ns
            is_missing = timestamp not in actual
            if is_missing:
                if missing_start is None or not contiguous:
                    if missing_start is not None and last_missing is not None:
                        ranges.extend(CashFutureGapDownloadPlanner._split_range(missing_start, last_missing, interval_ns, max_request_ns))
                    missing_start = timestamp
                last_missing = timestamp
            elif missing_start is not None and last_missing is not None:
                ranges.extend(CashFutureGapDownloadPlanner._split_range(missing_start, last_missing, interval_ns, max_request_ns))
                missing_start = None
                last_missing = None
            previous_expected = timestamp
        if missing_start is not None and last_missing is not None:
            ranges.extend(CashFutureGapDownloadPlanner._split_range(missing_start, last_missing, interval_ns, max_request_ns))
        return tuple(ranges)

    @staticmethod
    def _split_range(start: int, end: int, interval_ns: int, max_request_ns: int) -> tuple[tuple[int, int], ...]:
        parts: list[tuple[int, int]] = []
        cursor = start
        while cursor <= end:
            part_end = min(end, cursor + max_request_ns - 1)
            part_end -= (part_end - cursor) % interval_ns
            parts.append((cursor, part_end))
            cursor = part_end + interval_ns
        return tuple(parts)

    def _requests_for(self, request: HistoricalFetchRequest, sessions: tuple[SessionWindow, ...], catalog) -> tuple[HistoricalFetchRequest, ...]:
        expected = self._expected(sessions, request, self.interval_ns)
        actual = set(catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=request.start_ns, end_ns=request.end_ns))
        return tuple(HistoricalFetchRequest(request.source, request.instrument, request.timeframe, start, end) for start, end in self._missing_ranges(expected, actual, self.interval_ns, self.max_request_ns))

    def _coverage_for(self, request: HistoricalFetchRequest, sessions: tuple[SessionWindow, ...], catalog) -> tuple[CoverageRange, ...]:
        expected = self._expected(sessions, request, self.interval_ns)
        if not expected:
            return ()
        actual = set(catalog.timestamps(source=request.source, instrument=request.instrument, timeframe=request.timeframe, start_ns=request.start_ns, end_ns=request.end_ns))
        expected_set = set(expected)
        observed = len(expected_set & actual)
        missing = len(expected_set - actual)
        return (CoverageRange(request.instrument, expected[0], expected[-1], len(expected), observed, missing, missing == 0),)

    def coverage_manifest(self, *, queue: CashFutureDownloadQueue, catalog, spot_sessions: tuple[SessionWindow, ...], future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None):
        """Build a session-aware coverage manifest for spot and every exact future leg."""
        future_sessions = future_sessions or {}
        ranges = list(self._coverage_for(queue.spot, spot_sessions, catalog))
        for item in queue.futures:
            ranges.extend(self._coverage_for(item, future_sessions.get(item.instrument, ()), catalog))
        return build_coverage_manifest(source=queue.spot.source, ranges=ranges)

    def plan(self, *, queue: CashFutureDownloadQueue, catalog, spot_sessions: tuple[SessionWindow, ...], future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None) -> HistoricalSyncPlan:
        """Return deterministic, session-only repair requests for spot and exact future tokens."""
        future_sessions = future_sessions or {}
        requests = list(self._requests_for(queue.spot, spot_sessions, catalog))
        for item in queue.futures:
            requests.extend(self._requests_for(item, future_sessions.get(item.instrument, ()), catalog))
        return HistoricalSyncPlan(tuple(sorted(requests, key=lambda item: (item.instrument, item.start_ns, item.end_ns))))
