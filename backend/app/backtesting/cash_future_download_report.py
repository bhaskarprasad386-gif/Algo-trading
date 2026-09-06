"""Durable completeness/progress reporting for Cash-Future historical downloads."""

from __future__ import annotations

from dataclasses import dataclass

from .cash_future_download_queue import CashFutureDownloadQueue
from .historical_catalog import HistoricalCatalog
from .historical_ingest import HistoricalFetchRequest
from .session_chunk_completeness import SessionChunk, SessionChunkCompleteness
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class HistoricalChunkStatus:
    instrument: str
    timeframe: str
    start_ns: int
    end_ns: int
    expected_timestamps: int
    actual_timestamps: int
    missing_timestamps: int
    first_missing_ns: int | None
    complete: bool


@dataclass(frozen=True)
class CashFutureDownloadProgressReport:
    mode: str
    spot: HistoricalChunkStatus
    futures: tuple[HistoricalChunkStatus, ...]

    @property
    def total_chunks(self) -> int:
        return 1 + len(self.futures)

    @property
    def complete_chunks(self) -> int:
        return int(self.spot.complete) + sum(int(item.complete) for item in self.futures)

    @property
    def incomplete_chunks(self) -> int:
        return self.total_chunks - self.complete_chunks

    @property
    def complete(self) -> bool:
        return self.incomplete_chunks == 0


class CashFutureDownloadReporter:
    """Build a point-in-time report without changing or downloading market data."""

    def __init__(self, catalog: HistoricalCatalog) -> None:
        self.catalog = catalog
        self.completeness = SessionChunkCompleteness(catalog)

    @staticmethod
    def _expected(sessions: tuple[SessionWindow, ...], interval_ns: int) -> set[int]:
        expected: set[int] = set()
        for session in sessions:
            timestamp = session.start_ns
            while timestamp <= session.end_ns:
                expected.add(timestamp)
                timestamp += interval_ns
        return expected

    def chunk_status(
        self,
        request: HistoricalFetchRequest,
        *,
        interval_ns: int,
        sessions: tuple[SessionWindow, ...],
    ) -> HistoricalChunkStatus:
        expected = self._expected(
            tuple(
                SessionWindow(
                    max(session.start_ns, request.start_ns),
                    min(session.end_ns, request.end_ns),
                )
                for session in sessions
                if max(session.start_ns, request.start_ns)
                <= min(session.end_ns, request.end_ns)
            ),
            interval_ns,
        )
        actual = set(
            self.catalog.timestamps(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                start_ns=request.start_ns,
                end_ns=request.end_ns,
            )
        )
        missing = expected - actual
        # Keep the completeness implementation as the final boolean authority.
        complete = self.completeness.is_complete(
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            interval_ns=interval_ns,
            chunk=SessionChunk(request.start_ns, request.end_ns),
            sessions=sessions,
        )
        return HistoricalChunkStatus(
            instrument=request.instrument,
            timeframe=request.timeframe,
            start_ns=request.start_ns,
            end_ns=request.end_ns,
            expected_timestamps=len(expected),
            actual_timestamps=len(expected - missing),
            missing_timestamps=len(missing),
            first_missing_ns=min(missing) if missing else None,
            complete=complete,
        )

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
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        future_sessions = future_sessions or {}
        spot = self.chunk_status(
            queue.spot,
            interval_ns=interval_ns,
            sessions=spot_sessions,
        )
        futures = tuple(
            self.chunk_status(
                item.request,
                interval_ns=interval_ns,
                sessions=future_sessions.get(item.request.instrument, ()),
            )
            for item in queue.futures
        )
        return CashFutureDownloadProgressReport(mode=mode, spot=spot, futures=futures)
