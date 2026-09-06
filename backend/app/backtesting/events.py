"""Generic event model for multi-resolution, event-driven backtesting."""

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
    """Immutable source observation; timestamp is always normalized to epoch ns."""

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
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("sequence cannot be negative")


@dataclass(frozen=True)
class EventReplayConfig:
    """Replay controls; replay never fabricates observations between source events."""

    timestamp_unit: str = "ns"
    latency_ns: int = 0
    include_event_types: frozenset[EventType] | None = None
    replay_step_ns: int | None = None

    def __post_init__(self) -> None:
        if self.timestamp_unit not in {"ns", "us", "ms", "s", "m", "h", "d"}:
            raise ValueError("timestamp_unit must be ns, us, ms, s, m, h, or d")
        if self.latency_ns < 0:
            raise ValueError("latency_ns cannot be negative")
        if self.replay_step_ns is not None and self.replay_step_ns <= 0:
            raise ValueError("replay_step_ns must be positive")

    def to_ns(self, timestamp: int) -> int:
        multipliers = {
            "ns": 1,
            "us": 1_000,
            "ms": 1_000_000,
            "s": 1_000_000_000,
            "m": 60_000_000_000,
            "h": 3_600_000_000_000,
            "d": 86_400_000_000_000,
        }
        return int(timestamp) * multipliers[self.timestamp_unit]

    def normalize_timestamp(self, timestamp: int) -> int:
        return self.to_ns(timestamp)
