"""Request-aware Cash-Future gap repair."""
from __future__ import annotations
from dataclasses import dataclass
from .cash_future_download_queue import CashFutureDownloadQueue
from .cash_future_gap_download import CashFutureGapDownloadPlanner
from .historical_ingest import HistoricalFetchRequest
from .session_gap_planner import SessionWindow

@dataclass(frozen=True)
class CashFutureGapRepairPlan:
    spot: tuple[HistoricalFetchRequest, ...]
    futures: tuple[HistoricalFetchRequest, ...]
    @property
    def requests(self): return self.spot + self.futures
    @property
    def total_requests(self): return len(self.requests)

class CashFutureGapRepairPlanner:
    def __init__(self, catalog) -> None: self._catalog = catalog
    def plan(self, *, queue: CashFutureDownloadQueue, interval_ns: int,
             spot_sessions: tuple[SessionWindow, ...],
             future_sessions: dict[str, tuple[SessionWindow, ...]] | None = None):
        fs = future_sessions or {}
        spans = [queue.spot.end_ns - queue.spot.start_ns + 1] + [x.request.end_ns - x.request.start_ns + 1 for x in queue.futures]
        p = CashFutureGapDownloadPlanner(interval_ns=interval_ns, max_request_ns=max(interval_ns, max(spans)))
        spot = p._requests_for(queue.spot, spot_sessions, self._catalog)
        futures = []
        for x in queue.futures:
            r = x.request
            futures.extend(p._requests_for(r, fs.get(r.instrument, spot_sessions), self._catalog))
        return CashFutureGapRepairPlan(tuple(spot), tuple(futures))
