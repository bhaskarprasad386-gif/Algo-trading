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
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int):
            raise TypeError("timestamp_ns must be an integer")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if not self.instrument.strip():
            raise ValueError("instrument is required")
        if self.sequence is not None and (isinstance(self.sequence, bool) or not isinstance(self.sequence, int)):
            raise TypeError("sequence must be an integer")
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("sequence cannot be negative")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if self.event_type == EventType.DEPTH:
            evidence = self.payload.get("queue_evidence")
            if evidence is not None:
                if not isinstance(evidence, (list, tuple)):
                    raise TypeError("queue_evidence must be a sequence")
                for item in evidence:
                    if not isinstance(item, Mapping):
                        raise TypeError("queue evidence entries must be mappings")
                    for field_name in ("executed_quantity", "cancelled_quantity_ahead"):
                        value = item.get(field_name, 0)
                        if isinstance(value, bool) or not isinstance(value, int):
                            raise TypeError(f"{field_name} must be an integer")
                        if value < 0:
                            raise ValueError(f"{field_name} must be non-negative")


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
        if isinstance(self.latency_ns, bool) or not isinstance(self.latency_ns, int):
            raise TypeError("latency_ns must be an integer")
        if self.latency_ns < 0:
            raise ValueError("latency_ns cannot be negative")
        if self.replay_step_ns is not None:
            if isinstance(self.replay_step_ns, bool) or not isinstance(self.replay_step_ns, int):
                raise TypeError("replay_step_ns must be an integer")
            if self.replay_step_ns <= 0:
                raise ValueError("replay_step_ns must be positive")

    def to_ns(self, timestamp: int) -> int:
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise TypeError("timestamp must be an integer")
        multipliers = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000, "m": 60_000_000_000, "h": 3_600_000_000_000, "d": 86_400_000_000_000}
        return timestamp * multipliers[self.timestamp_unit]

    def normalize_timestamp(self, timestamp: int) -> int:
        return self.to_ns(timestamp)
