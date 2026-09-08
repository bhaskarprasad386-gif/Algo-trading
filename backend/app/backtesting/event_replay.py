"""Deterministic streaming ordering for tick and event replay."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplayEvent:
    timestamp_ns: int
    sequence: int
    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if self.sequence < 0:
            raise ValueError("sequence cannot be negative")


class DeterministicEventReplay:
    """Replay timestamped events deterministically without inventing precision."""

    @staticmethod
    def order(events: Iterable[ReplayEvent]) -> Iterator[ReplayEvent]:
        """Yield in timestamp/sequence order; input is materialized only for sorting."""
        yield from sorted(events, key=lambda event: (event.timestamp_ns, event.sequence))

    @staticmethod
    def validate(events: Iterable[ReplayEvent]) -> tuple[ReplayEvent, ...]:
        ordered = tuple(DeterministicEventReplay.order(events))
        DeterministicEventReplay._validate_unique(ordered)
        return ordered

    @staticmethod
    def _validate_unique(events: Iterable[ReplayEvent]) -> None:
        previous: tuple[int, int] | None = None
        for event in events:
            identity = (event.timestamp_ns, event.sequence)
            if identity == previous:
                raise ValueError("duplicate event timestamp/sequence identity")
            previous = identity

    @staticmethod
    def validate_ordered(events: Iterable[ReplayEvent]) -> Iterator[ReplayEvent]:
        """Validate an already timestamp/sequence-sorted stream without buffering it."""
        previous: tuple[int, int] | None = None
        for event in events:
            identity = (event.timestamp_ns, event.sequence)
            if previous is not None and identity < previous:
                raise ValueError("events are not in deterministic timestamp/sequence order")
            if identity == previous:
                raise ValueError("duplicate event timestamp/sequence identity")
            previous = identity
            yield event
