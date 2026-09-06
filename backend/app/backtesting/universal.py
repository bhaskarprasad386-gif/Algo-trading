"""Universal, strategy-agnostic event replay primitives.

The canonical timestamp is integer epoch nanoseconds. This module deliberately
stores source events without inventing higher-frequency observations.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence


@dataclass(frozen=True, order=True)
class MarketEvent:
    timestamp_ns: int
    instrument: str
    sequence: int = 0
    event_type: str = "quote"
    data: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if not self.instrument:
            raise ValueError("instrument is required")

    @property
    def context(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass(frozen=True)
class ReplayConfig:
    """Replay configuration; ``step_ns`` is a scheduling/view step, not fake data."""

    step_ns: int = 60_000_000_000

    def __post_init__(self) -> None:
        if self.step_ns <= 0:
            raise ValueError("step_ns must be positive")


class EventStrategy(Protocol):
    """Stable interface implemented by any backtestable strategy."""

    def on_event(self, event: MarketEvent, history: Sequence[MarketEvent]) -> Iterable[dict[str, Any]]:
        ...


def normalize_event(event: MarketEvent) -> MarketEvent:
    """Return an immutable canonical event while preserving source timestamp."""
    return MarketEvent(
        timestamp_ns=int(event.timestamp_ns),
        instrument=event.instrument,
        sequence=int(event.sequence),
        event_type=event.event_type,
        data=tuple(event.data),
    )


def ordered_events(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    """Deterministically order events; same-timestamp events use sequence identity."""
    normalized = [normalize_event(event) for event in events]
    return sorted(normalized, key=lambda event: (event.timestamp_ns, event.instrument, event.sequence))


def validate_source_resolution(events: Iterable[MarketEvent], minimum_timestamp_delta_ns: int) -> None:
    """Validate claimed source precision without manufacturing missing events."""
    if minimum_timestamp_delta_ns <= 0:
        raise ValueError("minimum_timestamp_delta_ns must be positive")
    ordered = ordered_events(events)
    for previous, current in zip(ordered, ordered[1:]):
        if previous.instrument == current.instrument and current.timestamp_ns > previous.timestamp_ns:
            if current.timestamp_ns - previous.timestamp_ns < minimum_timestamp_delta_ns:
                return
    if ordered and minimum_timestamp_delta_ns < 1_000:
        # A nanosecond timestamp alone is not evidence of sub-microsecond data.
        raise ValueError("source data does not prove the requested finer resolution")


def replay(events: Iterable[MarketEvent], strategy: EventStrategy) -> list[dict[str, Any]]:
    """Replay only real supplied events; no synthetic intermediate timestamps."""
    history: list[MarketEvent] = []
    decisions: list[dict[str, Any]] = []
    for event in ordered_events(events):
        emitted = strategy.on_event(event, tuple(history))
        decisions.extend(dict(decision) for decision in emitted)
        history.append(event)
    return decisions
