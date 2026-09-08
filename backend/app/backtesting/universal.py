"""Universal strategy-agnostic event replay primitives."""
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
        if self.timestamp_ns < 0: raise ValueError("timestamp_ns must be non-negative")
        if not self.instrument: raise ValueError("instrument is required")
    @property
    def context(self) -> dict[str, Any]: return dict(self.data)

@dataclass(frozen=True)
class ReplayConfig:
    step_ns: int = 60_000_000_000
    def __post_init__(self) -> None:
        if self.step_ns <= 0: raise ValueError("step_ns must be positive")

class EventStrategy(Protocol):
    def on_event(self, event: MarketEvent, history: Sequence[MarketEvent]) -> Iterable[dict[str, Any]]: ...

def normalize_event(event: MarketEvent) -> MarketEvent:
    return MarketEvent(int(event.timestamp_ns), event.instrument, int(event.sequence), event.event_type, tuple(event.data))

def ordered_events(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    normalized = [normalize_event(event) for event in events]
    return sorted(normalized, key=lambda event: (event.timestamp_ns, event.instrument, event.sequence))

def streaming_events(events: Iterable[MarketEvent]) -> Iterable[MarketEvent]:
    """O(1)-event-memory replay for sources already ordered by timestamp/instrument/sequence."""
    previous_key = None
    seen = set()
    for source_event in events:
        event = normalize_event(source_event)
        key = (event.timestamp_ns, event.instrument, event.sequence)
        if key in seen: raise ValueError("duplicate replay event identity")
        if previous_key is not None and key < previous_key: raise ValueError("stream is not deterministically ordered")
        seen.add(key)
        previous_key = key
        yield event

def validate_source_resolution(events: Iterable[MarketEvent], minimum_timestamp_delta_ns: int) -> None:
    if minimum_timestamp_delta_ns <= 0: raise ValueError("minimum_timestamp_delta_ns must be positive")
    ordered = ordered_events(events)
    for previous, current in zip(ordered, ordered[1:]):
        if previous.instrument == current.instrument and current.timestamp_ns > previous.timestamp_ns and current.timestamp_ns - previous.timestamp_ns < minimum_timestamp_delta_ns:
            return
    if ordered and minimum_timestamp_delta_ns < 1_000: raise ValueError("source data does not prove the requested finer resolution")

def replay(events: Iterable[MarketEvent], strategy: EventStrategy) -> list[dict[str, Any]]:
    history: list[MarketEvent] = []
    decisions: list[dict[str, Any]] = []
    for event in ordered_events(events):
        emitted = strategy.on_event(event, tuple(history))
        decisions.extend(dict(decision) for decision in emitted)
        history.append(event)
    return decisions
