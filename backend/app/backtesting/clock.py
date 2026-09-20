"""Deterministic time contracts for backtesting.

Backtests must advance from supplied event timestamps rather than wall-clock time.
Paper/live implementations can provide their own clock behind the same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockProtocol(Protocol):
    @property
    def now_ns(self) -> int:
        ...

    def advance_to(self, timestamp_ns: int) -> int:
        """Advance to a timestamp; moving backwards is forbidden."""
        ...


@dataclass
class BacktestClock:
    """Monotonic deterministic clock driven exclusively by event timestamps."""

    _now_ns: int = 0

    def __post_init__(self) -> None:
        if isinstance(self._now_ns, bool) or not isinstance(self._now_ns, int) or self._now_ns < 0:
            raise ValueError("initial clock timestamp must be a non-negative integer")

    @property
    def now_ns(self) -> int:
        return self._now_ns

    def advance_to(self, timestamp_ns: int) -> int:
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < 0:
            raise ValueError("clock timestamp must be a non-negative integer")
        if timestamp_ns < self._now_ns:
            raise ValueError("clock cannot move backwards")
        self._now_ns = timestamp_ns
        return self._now_ns
