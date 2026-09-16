"""Universal strategy-agnostic event replay primitives."""
from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Protocol, Sequence


@dataclass(frozen=True, order=True)
class MarketEvent:
    timestamp_ns: int
    instrument: str
    sequence: int = 0
    event_type: str = "quote"
    data: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int):
            raise TypeError("timestamp_ns must be an integer")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument is required")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise ValueError("event_type is required")
        if not isinstance(self.data, tuple):
            raise TypeError("data must be a tuple of key/value pairs")
        for item in self.data:
            if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
                raise TypeError("data entries must be (string_key, value) tuples")

    @property
    def context(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass(frozen=True)
class ReplayConfig:
    step_ns: int = 60_000_000_000

    def __post_init__(self) -> None:
        if isinstance(self.step_ns, bool) or not isinstance(self.step_ns, int):
            raise TypeError("step_ns must be an integer")
        if self.step_ns <= 0:
            raise ValueError("step_ns must be positive")


class EventStrategy(Protocol):
    def on_event(self, event: MarketEvent, history: Sequence[MarketEvent]) -> Iterable[dict[str, Any]]: ...


def normalize_event(event: MarketEvent) -> MarketEvent:
    return MarketEvent(event.timestamp_ns, event.instrument.strip(), event.sequence, event.event_type.strip(), tuple(event.data))


def _event_key(event: MarketEvent) -> tuple[object, ...]:
    """Build a deterministic total ordering for simultaneous events."""
    return (event.timestamp_ns, event.sequence, event.instrument, event.event_type, repr(event.data))


def ordered_events(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    normalized = [normalize_event(event) for event in events]
    return sorted(normalized, key=_event_key)


def streaming_events(events: Iterable[MarketEvent]) -> Iterable[MarketEvent]:
    """O(1)-event-memory iterator for sources ordered by durable replay identity."""
    previous_key = None
    for source_event in events:
        event = normalize_event(source_event)
        key = _event_key(event)
        if previous_key is not None and key < previous_key:
            raise ValueError("stream is not deterministically ordered")
        previous_key = key
        yield event


def validate_source_resolution(events: Iterable[MarketEvent], minimum_timestamp_delta_ns: int) -> None:
    """Require one observed same-instrument interval at or below the requested resolution."""
    if isinstance(minimum_timestamp_delta_ns, bool) or not isinstance(minimum_timestamp_delta_ns, int):
        raise TypeError("minimum_timestamp_delta_ns must be an integer")
    if minimum_timestamp_delta_ns <= 0:
        raise ValueError("minimum_timestamp_delta_ns must be positive")
    previous_by_instrument: dict[str, int] = {}
    for source_event in streaming_events(events):
        previous = previous_by_instrument.get(source_event.instrument)
        if previous is not None:
            delta = source_event.timestamp_ns - previous
            if 0 < delta <= minimum_timestamp_delta_ns:
                return
        previous_by_instrument[source_event.instrument] = source_event.timestamp_ns
    raise ValueError("source data does not prove the requested timestamp resolution")


def replay(events: Iterable[MarketEvent], strategy: EventStrategy) -> list[dict[str, Any]]:
    history: list[MarketEvent] = []
    decisions: list[dict[str, Any]] = []
    for event in ordered_events(events):
        emitted = strategy.on_event(event, tuple(history))
        decisions.extend(dict(decision) for decision in emitted)
        history.append(event)
    return decisions
