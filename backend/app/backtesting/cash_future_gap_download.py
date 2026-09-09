"""Session-aware Cash-Future gap download planning."""

from __future__ import annotations

from dataclasses import dataclass

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
    def _missing_ranges(
        expected: tuple[int, ...],
        actual: set[int],
        interval_ns: int,
        max_request_ns: int,
    ) -> tuple[tuple[int, int], ...]:
        ranges: list[tuple[int, int]] = []
        start: int | None = None
        previous: int | None = None
        for timestamp in expected:
            missing = timestamp not in actual
            contiguous = previous is not None and timestamp - previous == interval_ns
            if missing and (start is None or not contiguous):
                if start is not None and previous is not None:
                    ranges.extend(CashFutureGapDownloadPlanner._split_range(start, previous, interval_ns, max_request_ns))
                start = timestamp
            elif not missing and start is not None and previous is not None:
                ranges.extend(CashFutureGapDownloadPlanner._split_range(start, previous, interval_ns, max_request_ns))
                start = None
            previous = timestamp
        if start is not None and previous is not None:
            ranges.extend(CashFutureGapDownloadPlanner._split_range(start, previous, interval_ns, max_request_ns))
        return tuple(ranges)

    @staticmethod
    def _split_range(start: int, end: int, interval_ns: int, max_request_ns: int) -> tuple[tuple[int, int], ...]:
        parts: list[tuple[int, int]] = []
        cursor = start
        while cursor <= end:
            part_end = min(end, cursor + max_request_ns - 1)
            # Keep request boundaries on the expected cadence.
            part_end -= (part_end - cursor) % interval_ns
            parts.append((cursor, part_end))
            cursor = part_end + interval_ns
        return tuple(parts)

    def _requests_for(
        self,
        request: HistoricalFetchRequest,
        sessions: tuple[SessionWindow, ...],
        catalog,
    ) -> tuple[HistoricalFetchRequest, ...]:
        expected = self._expected(sessions, request, self.interval_ns)
        actual = set(catalog.timestamps(
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            start_ns=request.start_ns,
            end_ns=request.end_ns,
        ))
        return tuple(
            HistoricalFetchRequest(request.source, request.instrument, request.timeframe, start, end)
            for start, end in self._missing_ranges(expected, actual, self.interval_ns, self.max_request_ns)
        )

    def plan(
        self,
        *,
        queue: CashFutureDownloadQueue,
        catalog,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None,
    ) -> HistoricalSyncPlan:
        """Return deterministic, session-only repair requests for spot and exact future tokens."""
        future_sessions = future_sessions or {}
        requests = list(self._requests_for(queue.spot, spot_sessions, catalog))
        for item in queue.futures:
            requests.extend(self._requests_for(
                item.request,
                future_sessions.get(item.request.instrument, ()),
                catalog,
            ))
        return HistoricalSyncPlan(tuple(sorted(
            requests,
            key=lambda item: (item.instrument, item.start_ns, item.end_ns),
        )))
