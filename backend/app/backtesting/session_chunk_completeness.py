"""Session-aware completeness checks for resumable historical download chunks."""

from __future__ import annotations

from dataclasses import dataclass

from .historical_catalog import HistoricalCatalog
from .session_gap_planner import SessionWindow


@dataclass(frozen=True)
class SessionChunk:
    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        if self.start_ns < 0 or self.end_ns < self.start_ns:
            raise ValueError("invalid chunk range")


class SessionChunkCompleteness:
    """Prove completeness by enumerating every expected timestamp in each session."""

    def __init__(self, catalog: HistoricalCatalog) -> None:
        self.catalog = catalog

    @staticmethod
    def expected_timestamps(
        sessions: tuple[SessionWindow, ...], interval_ns: int
    ) -> set[int]:
        """Return the exact cadence expected inside the supplied market sessions."""
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        expected: set[int] = set()
        for session in sessions:
            timestamp = session.start_ns
            while timestamp <= session.end_ns:
                expected.add(timestamp)
                timestamp += interval_ns
        return expected

    def is_complete(
        self,
        *,
        source: str,
        instrument: str,
        timeframe: str,
        interval_ns: int,
        chunk: SessionChunk,
        sessions: tuple[SessionWindow, ...],
    ) -> bool:
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        relevant = tuple(
            SessionWindow(
                max(session.start_ns, chunk.start_ns),
                min(session.end_ns, chunk.end_ns),
            )
            for session in sessions
            if max(session.start_ns, chunk.start_ns) <= min(session.end_ns, chunk.end_ns)
        )
        if not relevant:
            return True

        expected = self.expected_timestamps(relevant, interval_ns)
        if not expected:
            return True
        actual = set(
            self.catalog.timestamps(
                source=source,
                instrument=instrument,
                timeframe=timeframe,
                start_ns=min(expected),
                end_ns=max(expected),
            )
        )
        return expected.issubset(actual)
