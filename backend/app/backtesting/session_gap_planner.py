"""Session-aware historical gap planning."""

from __future__ import annotations

from dataclasses import dataclass

from .historical_catalog import Gap, HistoricalCatalog


@dataclass(frozen=True)
class SessionWindow:
    """Inclusive expected-data window expressed in nanoseconds."""

    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        if self.start_ns < 0 or self.end_ns < self.start_ns:
            raise ValueError("invalid session window")


class SessionAwareGapPlanner:
    """Plan repairs only inside explicitly expected market sessions."""

    def __init__(self, catalog: HistoricalCatalog) -> None:
        self.catalog = catalog

    def plan(
        self,
        *,
        source: str,
        instrument: str,
        timeframe: str,
        interval_ns: int,
        sessions: tuple[SessionWindow, ...],
    ) -> tuple[Gap, ...]:
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        raw_gaps = self.catalog.gaps(
            source=source,
            instrument=instrument,
            timeframe=timeframe,
            interval_ns=interval_ns,
        )
        planned: list[Gap] = []
        for gap in raw_gaps:
            for session in sorted(sessions, key=lambda item: item.start_ns):
                start = max(gap.start_ns, session.start_ns)
                end = min(gap.end_ns, session.end_ns)
                if start <= end:
                    planned.append(Gap(gap.instrument, gap.timeframe, start, end))
        return tuple(planned)
