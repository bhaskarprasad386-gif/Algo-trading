"""Explicit expected-event comparison for non-cadenced historical data."""

from __future__ import annotations

from collections.abc import Iterable

from .historical_catalog import HistoricalCatalog


def missing_expected_timestamps(
    catalog: HistoricalCatalog,
    *,
    source: str,
    instrument: str,
    timeframe: str,
    expected_timestamps: Iterable[int],
) -> tuple[int, ...]:
    """Return only explicitly expected timestamps that are absent from the catalog.

    Unlike cadence-based gap detection, this never assumes that events occur at a
    fixed interval. It is therefore suitable for tick, quote, depth and event data
    when an authoritative expected-event set is available.
    """
    expected = tuple(sorted(set(int(value) for value in expected_timestamps)))
    if any(value < 0 for value in expected):
        raise ValueError("expected timestamps cannot be negative")
    if not expected:
        return ()

    observed = set(
        catalog.timestamps(
            source=source,
            instrument=instrument,
            timeframe=timeframe,
            start_ns=expected[0],
            end_ns=expected[-1],
        )
    )
    return tuple(timestamp for timestamp in expected if timestamp not in observed)
