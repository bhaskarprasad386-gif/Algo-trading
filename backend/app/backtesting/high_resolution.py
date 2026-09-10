"""High-resolution market-data helpers.

The historical catalog stores nanosecond timestamps already. This module keeps
high-resolution/event data separate from cadence-based bar completeness rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class EventDataSpec:
    """Describe event/tick data without assuming a fixed cadence."""

    timeframe: str = "tick"
    timestamp_unit: str = "ns"
    ordered_by_sequence: bool = True

    def __post_init__(self) -> None:
        if not self.timeframe.strip():
            raise ValueError("timeframe is required")
        if self.timestamp_unit != "ns":
            raise ValueError("timestamp_unit must be 'ns'")


def is_event_timeframe(timeframe: str) -> bool:
    """Return True for non-cadenced market-event timeframes."""
    return timeframe.strip().lower() in {"tick", "ticks", "event", "events", "orderbook", "order_book"}


def event_identity(*, timestamp_ns: int, sequence: int | None = None) -> tuple[int, int | None]:
    """Return the stable identity components for same-timestamp events."""
    if timestamp_ns < 0:
        raise ValueError("timestamp_ns cannot be negative")
    if sequence is not None and sequence < 0:
        raise ValueError("sequence cannot be negative")
    return timestamp_ns, sequence


def validate_event_payload(payload: Mapping[str, Any]) -> None:
    """Validate only structural requirements; provider-specific fields remain untouched."""
    if not isinstance(payload, Mapping):
        raise TypeError("event payload must be a mapping")
