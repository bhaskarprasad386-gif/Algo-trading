"""Safe skip callback for resumable historical download chunks."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .historical_ingest import HistoricalFetchRequest
from .session_chunk_completeness import SessionChunk, SessionChunkCompleteness
from .session_gap_planner import SessionWindow


_INTERVAL_NS = {
    "1m": 60 * 1_000_000_000,
    "3m": 3 * 60 * 1_000_000_000,
    "5m": 5 * 60 * 1_000_000_000,
    "10m": 10 * 60 * 1_000_000_000,
    "15m": 15 * 60 * 1_000_000_000,
    "30m": 30 * 60 * 1_000_000_000,
    "1h": 60 * 60 * 1_000_000_000,
    "1d": 24 * 60 * 60 * 1_000_000_000,
}


class SessionChunkSkipPolicy:
    """Skip only chunks proven complete by an explicit session provider."""

    def __init__(
        self,
        completeness: SessionChunkCompleteness,
        session_provider: Callable[[HistoricalFetchRequest], Iterable[SessionWindow]],
    ) -> None:
        self.completeness = completeness
        self.session_provider = session_provider

    def __call__(self, request: HistoricalFetchRequest) -> bool:
        interval_ns = _INTERVAL_NS.get(request.timeframe)
        if interval_ns is None:
            raise ValueError(f"unsupported timeframe for completeness: {request.timeframe}")
        sessions = tuple(self.session_provider(request))
        return self.completeness.is_complete(
            source=request.source,
            instrument=request.instrument,
            timeframe=request.timeframe,
            interval_ns=interval_ns,
            chunk=SessionChunk(request.start_ns, request.end_ns),
            sessions=sessions,
        )
