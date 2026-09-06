"""Generic event model for multi-resolution, event-driven backtesting.

The model deliberately keeps the payload strategy-agnostic. A strategy may
consume candles, quotes, trades, depth updates, or any combination of events.
Timestamps are integer epoch nanoseconds when available; epoch milliseconds
are also accepted and normalized explicitly by the caller.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class EventType(str, Enum):
    BAR = "bar"
    QUOTE = "quote"
    TRADE = "trade"
    DEPTH = "depth"
    CUSTOM = "custom"


@dataclass(frozen=True)
class MarketEvent:
    """Immutable normalized market event used by the replay engine."""

    timestamp_ns: int
    instrument: str
    event_type: EventType
    payload: Mapping[str, Any] = field(default_factory=dict)
    sequence: int | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if not self.instrument.strip():
            raise ValueError("instrument is required")


@dataclass(frozen=True)
class EventReplayConfig:
    """Replay controls without inventing events between source observations."""

    timestamp_unit: str = "ns"
    latency_ns: int = 0
    include_event_types: frozenset[EventType] | None = None

    def __post_init__(self) -> None:
        if self.timestamp_unit not in {"ns", "us", "ms"}:
            raise ValueError("timestamp_unit must be ns, us, or ms")
        if self.latency_ns < 0:
            raise ValueError("latency_ns cannot be negative")

    def to_ns(self, timestamp: int) -> int:
        multipliers = {"ns": 1, "us": 1_000, "ms": 1_000_000}
        return int(timestamp) * multipliers[self.timestamp_unit]
