from __future__ import annotations

from dataclasses import dataclass

from .cash_future_download_queue import CashFutureDownloadQueue
from .historical_ingest import HistoricalFetchRequest
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class CashFutureDownloadChunkStatus:
    instrument: str
    source: str
    timeframe: str
    start_ns: int
    end_ns: int
    expected: int
    present: int
    missing: int
    complete: bool
    first_missing_ns: int | None = None

    @property
    def expected_timestamps(self) -> int:
        return self.expected

    @property
    def actual_timestamps(self) -> int:
        return self.present

    @property
    def missing_timestamps(self) -> int:
        return self.missing


@dataclass(frozen=True)
class CashFutureDownloadProgressReport:
    mode: str
    spot: tuple[CashFutureDownloadChunkStatus, ...]
    futures: tuple[CashFutureDownloadChunkStatus, ...]

    def _chunks(self) -> tuple[CashFutureDownloadChunkStatus, ...]:
        return (*self.spot, *self.futures)

    @property
    def total_chunks(self) -> int:
        return len(self._chunks())

    @property
    def complete_chunks(self) -> int:
        return sum(chunk.complete for chunk in self._chunks())

    @property
    def incomplete_chunks(self) -> int:
        return self.total_chunks - self.complete_chunks

    @property
    def complete(self) -> bool:
        return all(chunk.complete for chunk in self._chunks())


class CashFutureDownloadReporter:
    def __init__(self, catalog):
        self.catalog = catalog

    def _chunk_statuses(
        self,
        request: HistoricalFetchRequest,
        *,
        interval_ns: int,
        sessions: tuple[SessionWindow, ...],
    ) -> tuple[CashFutureDownloadChunkStatus, ...]:
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")

        statuses = []
        for session in sessions:
            start_ns = max(session.start_ns, request.start_ns)
            end_ns = min(session.end_ns, request.end_ns)
            if start_ns > end_ns:
                continue
            expected = max(0, ((end_ns - start_ns) // interval_ns) + 1)
            timestamps = tuple(
                self.catalog.timestamps(
                    source=request.source,
                    instrument=request.instrument,
                    timeframe=request.timeframe,
                    start_ns=start_ns,
                    end_ns=end_ns,
                )
            )
            present_timestamps = set(timestamps)
            first_missing = next(
                (
                    start_ns + offset * interval_ns
                    for offset in range(expected)
                    if start_ns + offset * interval_ns not in present_timestamps
                ),
                None,
            )
            present = len(timestamps)
            statuses.append(
                CashFutureDownloadChunkStatus(
                    instrument=request.instrument,
                    source=request.source,
                    timeframe=request.timeframe,
                    start_ns=start_ns,
                    end_ns=end_ns,
                    expected=expected,
                    present=present,
                    missing=max(0, expected - present),
                    complete=present >= expected,
                    first_missing_ns=first_missing,
                )
            )
        return tuple(statuses)

    def chunk_status(
        self,
        request: HistoricalFetchRequest,
        *,
        interval_ns: int,
        sessions: tuple[SessionWindow, ...],
    ) -> CashFutureDownloadChunkStatus:
        """Return one aggregate status for the supplied request/session set."""
        statuses = self._chunk_statuses(
            request, interval_ns=interval_ns, sessions=sessions
        )
        if not statuses:
            return CashFutureDownloadChunkStatus(
                instrument=request.instrument,
                source=request.source,
                timeframe=request.timeframe,
                start_ns=request.start_ns,
                end_ns=request.end_ns,
                expected=0,
                present=0,
                missing=0,
                complete=True,
            )

        return CashFutureDownloadChunkStatus(
            instrument=request.instrument,
            source=request.source,
            timeframe=request.timeframe,
            start_ns=min(status.start_ns for status in statuses),
            end_ns=max(status.end_ns for status in statuses),
            expected=sum(status.expected for status in statuses),
            present=sum(status.present for status in statuses),
            missing=sum(status.missing for status in statuses),
            complete=all(status.complete for status in statuses),
            first_missing_ns=next(
                (
                    status.first_missing_ns
                    for status in statuses
                    if status.first_missing_ns is not None
                ),
                None,
            ),
        )

    @staticmethod
    def _spot_request(queue: CashFutureDownloadQueue) -> HistoricalFetchRequest:
        """Accept both direct HistoricalFetchRequest and wrapped spot payloads."""
        return getattr(queue.spot, "request", queue.spot)

    def report(
        self,
        *,
        queue: CashFutureDownloadQueue,
        mode: str,
        interval_ns: int,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None,
    ) -> CashFutureDownloadProgressReport:
        """Report spot and exact-token future coverage for the supplied queue."""
        future_sessions = future_sessions or {}
        spot = self._chunk_statuses(
            self._spot_request(queue),
            interval_ns=interval_ns,
            sessions=spot_sessions,
        )
        futures = tuple(
            status
            for item in queue.futures
            for status in self._chunk_statuses(
                item.request,
                interval_ns=interval_ns,
                sessions=future_sessions.get(item.request.instrument, ()),
            )
        )
        return CashFutureDownloadProgressReport(mode=mode, spot=spot, futures=futures)
