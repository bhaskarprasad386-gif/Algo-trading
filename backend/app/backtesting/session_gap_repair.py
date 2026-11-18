"""Session-aware planning for historical cadence-gap repairs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .historical_catalog import Gap


@dataclass(frozen=True)
class RepairRange:
    """A contiguous source fetch range containing expected timestamps."""

    instrument: str
    timeframe: str
    start_ns: int
    end_ns: int


class SessionGapRepairPlanner:
    """Filter generic cadence gaps through an explicit market-session calendar."""

    def __init__(self, is_expected_timestamp_ns: Callable[[int], bool]) -> None:
        self._is_expected = is_expected_timestamp_ns

    def plan(
        self,
        gaps: tuple[Gap, ...] | list[Gap],
        *,
        interval_ns: int,
    ) -> tuple[RepairRange, ...]:
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")

        planned: list[RepairRange] = []
        for gap in gaps:
            if gap.end_ns < gap.start_ns:
                continue

            run_start: int | None = None
            previous_expected: int | None = None
            timestamp = gap.start_ns
            while timestamp <= gap.end_ns:
                expected = bool(self._is_expected(timestamp))
                if expected and run_start is None:
                    run_start = timestamp
                elif not expected and run_start is not None:
                    planned.append(
                        RepairRange(
                            gap.instrument,
                            gap.timeframe,
                            run_start,
                            previous_expected if previous_expected is not None else run_start,
                        )
                    )
                    run_start = None
                if expected:
                    previous_expected = timestamp
                timestamp += interval_ns

            if run_start is not None:
                planned.append(
                    RepairRange(
                        gap.instrument,
                        gap.timeframe,
                        run_start,
                        previous_expected if previous_expected is not None else run_start,
                    )
                )

        return tuple(planned)
