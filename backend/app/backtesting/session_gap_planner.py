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
    """Plan repairs only for cadence gaps fully contained in expected sessions."""

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

        planned: list[Gap] = []
        for session in sorted(sessions, key=lambda item: item.start_ns):
            timestamps = self.catalog.timestamps(
                source=source,
                instrument=instrument,
                timeframe=timeframe,
                start_ns=session.start_ns,
                end_ns=session.end_ns,
            )
            for previous, current in zip(timestamps, timestamps[1:]):
                if current - previous > interval_ns:
                    planned.append(
                        Gap(
                            instrument,
                            timeframe,
                            previous + interval_ns,
                            current - interval_ns,
                        )
                    )
        return tuple(planned)
