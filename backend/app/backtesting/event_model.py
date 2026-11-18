"""Deterministic event identity and ordering contracts."""

from __future__ import annotations

from dataclasses import dataclass

from app.backtesting.historical_catalog import HistoricalRecord


@dataclass(frozen=True, order=True)
class EventIdentity:
    """Stable identity for one source event."""

    timestamp_ns: int
    source: str
    instrument: str
    timeframe: str
    sequence: int | None = None

    @classmethod
    def from_record(cls, record: HistoricalRecord) -> "EventIdentity":
        return cls(record.timestamp_ns, record.source, record.instrument, record.timeframe, record.sequence)


def event_identity(record: HistoricalRecord) -> EventIdentity:
    return EventIdentity.from_record(record)


def event_order_key(record: HistoricalRecord) -> tuple[int, str, str, str, int]:
    return (
        record.timestamp_ns,
        record.source,
        record.instrument,
        record.timeframe,
        -1 if record.sequence is None else record.sequence,
    )
