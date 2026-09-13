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


@dataclass(frozen=True)
class CashFutureDownloadProgressReport:
    mode: str
    spot: tuple[CashFutureDownloadChunkStatus, ...]
    futures: tuple[CashFutureDownloadChunkStatus, ...]


class CashFutureDownloadReporter:
    def __init__(self, catalog):
        self.catalog = catalog

    def chunk_status(
        self,
        request: HistoricalFetchRequest,
        *,
        interval_ns: int,
        sessions: tuple[SessionWindow, ...],
    ) -> tuple[CashFutureDownloadChunkStatus, ...]:
        statuses = []
        for session in sessions:
            expected = max(0, (session.end_ns - session.start_ns) // interval_ns)
            present = self.catalog.count(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                start_ns=session.start_ns,
                end_ns=session.end_ns,
            )
            statuses.append(
                CashFutureDownloadChunkStatus(
                    instrument=request.instrument,
                    source=request.source,
                    timeframe=request.timeframe,
                    start_ns=session.start_ns,
                    end_ns=session.end_ns,
                    expected=expected,
                    present=present,
                    missing=max(0, expected - present),
                    complete=present >= expected,
                )
            )
        return tuple(statuses)

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
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        future_sessions = future_sessions or {}
        spot = self.chunk_status(
            self._spot_request(queue),
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
