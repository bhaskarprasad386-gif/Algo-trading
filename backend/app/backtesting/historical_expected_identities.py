"""Explicit expected-event identity comparison for non-cadenced historical data."""

from __future__ import annotations

from collections.abc import Iterable

EventIdentity = tuple[int, int | None]


def missing_expected_identities(
    expected_identities: Iterable[EventIdentity],
    observed_identities: Iterable[EventIdentity],
) -> tuple[EventIdentity, ...]:
    """Return authoritative event identities absent from the observed dataset.

    Identity includes both canonical nanosecond timestamp and optional sequence,
    so multiple events sharing the same timestamp are independently checkable.
    No cadence is inferred and no timestamps are synthesized.
    """
    expected = tuple(sorted(set(expected_identities)))
    observed = set(observed_identities)
    for timestamp_ns, sequence in expected:
        if timestamp_ns < 0 or (sequence is not None and sequence < 0):
            raise ValueError("event timestamp and sequence cannot be negative")
    for timestamp_ns, sequence in observed:
        if timestamp_ns < 0 or (sequence is not None and sequence < 0):
            raise ValueError("event timestamp and sequence cannot be negative")
    return tuple(identity for identity in expected if identity not in observed)


__all__ = ["EventIdentity", "missing_expected_identities"]
