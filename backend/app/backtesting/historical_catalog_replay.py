"""Build deterministic strategy events directly from the persistent historical catalog.

This bridge deliberately uses exact source timestamps only. It never forward-fills,
back-fills, resamples, or invents a quote for a missing leg. A strategy event is
emitted only when every requested leg has an observation at that exact timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from .historical_catalog import HistoricalCatalog, HistoricalRecord


@dataclass(frozen=True)
class CatalogReplayLeg:
    """One historical catalog stream mapped into a strategy event key."""

    event_key: str
    source: str
    instrument: str
    timeframe: str

    def __post_init__(self) -> None:
        if not self.event_key.strip() or not self.source.strip() or not self.instrument.strip() or not self.timeframe.strip():
            raise ValueError("event_key, source, instrument and timeframe are required")


class HistoricalCatalogEventReplay:
    """Join persistent catalog streams into deterministic, point-in-time events."""

    def __init__(self, catalog: HistoricalCatalog) -> None:
        self.catalog = catalog

    def events(
        self,
        legs: tuple[CatalogReplayLeg, ...],
        *,
        start_ns: int,
        end_ns: int,
        require_complete: bool = True,
    ) -> Iterator[Mapping[str, Any]]:
        if not legs:
            raise ValueError("at least one replay leg is required")
        if start_ns < 0 or end_ns < start_ns:
            raise ValueError("invalid replay range")
        if len({leg.event_key for leg in legs}) != len(legs):
            raise ValueError("replay event keys must be unique")

        streams: dict[str, tuple[HistoricalRecord, ...]] = {}
        for leg in legs:
            streams[leg.event_key] = tuple(
                record
                for record in self.catalog.records(
                    source=leg.source,
                    instrument=leg.instrument,
                    timeframe=leg.timeframe,
                )
                if start_ns <= record.timestamp_ns <= end_ns
            )

        timestamps = sorted({record.timestamp_ns for records in streams.values() for record in records})
        by_leg: dict[str, dict[int, HistoricalRecord]] = {}
        for leg in legs:
            index: dict[int, HistoricalRecord] = {}
            for record in streams[leg.event_key]:
                if record.timestamp_ns in index:
                    raise ValueError(
                        f"multiple catalog records at one timestamp for replay leg: {leg.event_key}"
                    )
                index[record.timestamp_ns] = record
            by_leg[leg.event_key] = index

        for timestamp_ns in timestamps:
            missing = [leg.event_key for leg in legs if timestamp_ns not in by_leg[leg.event_key]]
            if missing:
                if require_complete:
                    continue
                yield {
                    "timestamp_ns": timestamp_ns,
                    "data_resolution": legs[0].timeframe,
                    **{
                        leg.event_key: by_leg[leg.event_key][timestamp_ns].payload
                        for leg in legs
                        if timestamp_ns in by_leg[leg.event_key]
                    },
                }
                continue
            yield {
                "timestamp_ns": timestamp_ns,
                "data_resolution": legs[0].timeframe,
                **{
                    leg.event_key: by_leg[leg.event_key][timestamp_ns].payload
                    for leg in legs
                },
            }


__all__ = ["CatalogReplayLeg", "HistoricalCatalogEventReplay"]
