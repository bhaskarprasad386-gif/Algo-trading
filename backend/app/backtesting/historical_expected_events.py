"""Explicit expected-event comparison for non-cadenced historical data."""

from __future__ import annotations

from collections.abc import Iterable

from .historical_catalog import HistoricalCatalog
from .historical_ingest import HistoricalFetchRequest


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


def build_expected_event_repair_plan(
    catalog: HistoricalCatalog,
    *,
    source: str,
    instrument: str,
    timeframe: str,
    expected_timestamps: Iterable[int],
    max_request_ns: int,
) -> tuple[HistoricalFetchRequest, ...]:
    """Build bounded provider fetch ranges for explicitly missing events.

    This groups authoritative missing timestamps into enclosing time ranges. It
    never invents an event cadence; providers may return additional source-backed
    events inside those ranges, which are handled normally by ingestion.
    """
    if max_request_ns <= 0:
        raise ValueError("max_request_ns must be positive")

    missing = missing_expected_timestamps(
        catalog,
        source=source,
        instrument=instrument,
        timeframe=timeframe,
        expected_timestamps=expected_timestamps,
    )
    if not missing:
        return ()

    requests: list[HistoricalFetchRequest] = []
    start = last = missing[0]
    for timestamp in missing[1:]:
        if timestamp - start > max_request_ns:
            requests.append(HistoricalFetchRequest(source, instrument, timeframe, start, last))
            start = timestamp
        last = timestamp
    requests.append(HistoricalFetchRequest(source, instrument, timeframe, start, last))
    return tuple(requests)
