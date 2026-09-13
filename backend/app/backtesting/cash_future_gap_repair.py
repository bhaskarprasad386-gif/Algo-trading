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

        # Accept both direct HistoricalFetchRequest objects and wrapped payloads.
        spot_req = getattr(queue.spot, "request", queue.spot)
        future_reqs = [getattr(x, "request", x) for x in queue.futures]

        spans = [spot_req.end_ns - spot_req.start_ns + 1] + [
            req.end_ns - req.start_ns + 1 for req in future_reqs
        ]
        p = CashFutureGapDownloadPlanner(
            interval_ns=interval_ns,
            max_request_ns=max(interval_ns, max(spans)),
        )
        spot = p._requests_for(spot_req, spot_sessions, self._catalog)
        futures = []
        for req in future_reqs:
            futures.extend(
                p._requests_for(
                    req,
                    fs.get(req.instrument, spot_sessions),
                    self._catalog,
                )
            )
        return CashFutureGapRepairPlan(tuple(spot), tuple(futures))
