"""Deterministic timestamp ordering for tick and event replay."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Iterator, Mapping
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
    """Replay events in exact timestamp/sequence order without fabricating precision."""

    @staticmethod
    def order(events: Iterable[ReplayEvent]) -> Iterator[ReplayEvent]:
        ordered = sorted(events, key=lambda event: (event.timestamp_ns, event.sequence))
        yield from ordered

    @staticmethod
    def validate(events: Iterable[ReplayEvent]) -> tuple[ReplayEvent, ...]:
        ordered = tuple(DeterministicEventReplay.order(events))
        seen: set[tuple[int, int]] = set()
        for event in ordered:
            identity = (event.timestamp_ns, event.sequence)
            if identity in seen:
                raise ValueError("duplicate event timestamp/sequence identity")
            seen.add(identity)
        return ordered
