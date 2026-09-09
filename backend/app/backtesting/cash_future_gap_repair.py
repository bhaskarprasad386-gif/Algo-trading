"""Deterministic Cash-Future gap-repair request planning."""

from __future__ import annotations

from dataclasses import dataclass

from .cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from .historical_ingest import HistoricalFetchRequest
from .session_gap_planner import SessionAwareGapPlanner, SessionWindow


@dataclass(frozen=True)
class CashFutureGapRepairPlan:
    """Download requests restricted to missing timestamps inside market sessions."""

    spot: tuple[HistoricalFetchRequest, ...]
    futures: tuple[HistoricalFetchRequest, ...]

    @property
    def requests(self) -> tuple[HistoricalFetchRequest, ...]:
        return self.spot + self.futures

    @property
    def total_requests(self) -> int:
        return len(self.requests)


class CashFutureGapRepairPlanner:
    """Convert stored Cash/Future gaps into bounded fetch requests without mutation."""

    def __init__(self, catalog) -> None:
        self._planner = SessionAwareGapPlanner(catalog)

    @staticmethod
    def _requests(source: str, instrument: str, timeframe: str, gaps) -> tuple[HistoricalFetchRequest, ...]:
        return tuple(
            HistoricalFetchRequest(source, instrument, timeframe, gap.start_ns, gap.end_ns)
            for gap in gaps
        )

    def plan(
        self,
        *,
        queue: CashFutureDownloadQueue,
        interval_ns: int,
        spot_sessions: tuple[SessionWindow, ...],
        future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None,
    ) -> CashFutureGapRepairPlan:
        future_sessions = future_sessions or {}
        spot = queue.spot
        spot_gaps = self._planner.plan(
            source=spot.source,
            instrument=spot.instrument,
            timeframe=spot.timeframe,
            interval_ns=interval_ns,
            sessions=spot_sessions,
        )
        future_requests: list[HistoricalFetchRequest] = []
        for item in queue.futures:
            request = item.request
            sessions = future_sessions.get(request.instrument, spot_sessions)
            gaps = self._planner.plan(
                source=request.source,
                instrument=request.instrument,
                timeframe=request.timeframe,
                interval_ns=interval_ns,
                sessions=sessions,
            )
            future_requests.extend(self._requests(request.source, request.instrument, request.timeframe, gaps))
        return CashFutureGapRepairPlan(
            spot=self._requests(spot.source, spot.instrument, spot.timeframe, spot_gaps),
            futures=tuple(future_requests),
        )
