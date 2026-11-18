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

    @staticmethod
    def _data_resolution(legs: tuple[CatalogReplayLeg, ...]) -> str:
        timeframes = tuple(dict.fromkeys(leg.timeframe for leg in legs))
        return timeframes[0] if len(timeframes) == 1 else "mixed"

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

        data_resolution = self._data_resolution(legs)
        streams = {
            leg.event_key: iter(
                self.catalog.iter_records(
                    source=leg.source,
                    instrument=leg.instrument,
                    timeframe=leg.timeframe,
                    start_ns=start_ns,
                    end_ns=end_ns,
                )
            )
            for leg in legs
        }
        current: dict[str, HistoricalRecord | None] = {}
        for leg in legs:
            current[leg.event_key] = next(streams[leg.event_key], None)

        while any(record is not None for record in current.values()):
            timestamp_ns = min(
                record.timestamp_ns
                for record in current.values()
                if record is not None
            )
            at_timestamp: dict[str, HistoricalRecord] = {}
            for leg in legs:
                record = current[leg.event_key]
                if record is None or record.timestamp_ns != timestamp_ns:
                    continue
                at_timestamp[leg.event_key] = record
                current[leg.event_key] = next(streams[leg.event_key], None)
                duplicate = current[leg.event_key]
                if duplicate is not None and duplicate.timestamp_ns == timestamp_ns:
                    raise ValueError(
                        f"multiple catalog records at one timestamp for replay leg: {leg.event_key}"
                    )

            replay_metadata = {
                leg.event_key: {
                    "source": leg.source,
                    "instrument": leg.instrument,
                    "timeframe": leg.timeframe,
                    "sequence": at_timestamp[leg.event_key].sequence,
                }
                for leg in legs
                if leg.event_key in at_timestamp
            }
            missing = [leg.event_key for leg in legs if leg.event_key not in at_timestamp]
            if missing:
                if require_complete:
                    continue
                yield {
                    "timestamp_ns": timestamp_ns,
                    "data_resolution": data_resolution,
                    "__replay_legs__": replay_metadata,
                    **{
                        leg.event_key: at_timestamp[leg.event_key].payload
                        for leg in legs
                        if leg.event_key in at_timestamp
                    },
                }
                continue
            yield {
                "timestamp_ns": timestamp_ns,
                "data_resolution": data_resolution,
                "__replay_legs__": replay_metadata,
                **{
                    leg.event_key: at_timestamp[leg.event_key].payload
                    for leg in legs
                },
            }


__all__ = ["CatalogReplayLeg", "HistoricalCatalogEventReplay"]
