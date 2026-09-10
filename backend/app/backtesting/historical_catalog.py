"""Persistent historical market-data catalog with incremental deduplication and gap detection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class HistoricalRecord:
    source: str
    instrument: str
    timeframe: str
    timestamp_ns: int
    payload: Mapping[str, Any]
    sequence: int | None = None

    def identity(self) -> tuple[str, str, str, int, int | None]:
        return (self.source, self.instrument, self.timeframe, self.timestamp_ns, self.sequence)


@dataclass(frozen=True)
class Gap:
    instrument: str
    timeframe: str
    start_ns: int
    end_ns: int


class HistoricalCatalog:
    """SQLite catalog that appends new market data and repairs gaps without replacing prior data."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS data_catalog (
            source TEXT NOT NULL, instrument TEXT NOT NULL, timeframe TEXT NOT NULL,
            timestamp_ns INTEGER NOT NULL, sequence INTEGER, payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL, ingested_at_ns INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(source, instrument, timeframe, timestamp_ns, sequence)
        )""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS source_watermarks (
            source TEXT NOT NULL, instrument TEXT NOT NULL, timeframe TEXT NOT NULL,
            max_timestamp_ns INTEGER NOT NULL,
            PRIMARY KEY(source, instrument, timeframe)
        )""")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _payload_json(payload: Mapping[str, Any]) -> str:
        return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _hash(payload_json: str) -> str:
        return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    def ingest(self, records: Iterable[HistoricalRecord], *, ingested_at_ns: int = 0) -> int:
        """Append a batch. New future records, late gap repairs and exact duplicates are all safe."""
        if ingested_at_ns < 0:
            raise ValueError("ingested_at_ns cannot be negative")
        rows = []
        for record in records:
            if not record.source.strip() or not record.instrument.strip() or not record.timeframe.strip():
                raise ValueError("source, instrument and timeframe are required")
            if record.timestamp_ns < 0 or (record.sequence is not None and record.sequence < 0):
                raise ValueError("timestamp_ns and sequence cannot be negative")
            payload_json = self._payload_json(record.payload)
            rows.append((*record.identity(), payload_json, self._hash(payload_json), ingested_at_ns))
        if not rows:
            return 0
        inserted = 0
        try:
            for row in rows:
                if row[4] is None:
                    existing = self._db.execute(
                        "SELECT payload_hash FROM data_catalog WHERE source=? AND instrument=? AND timeframe=? AND timestamp_ns=? AND sequence IS NULL",
                        row[:4],
                    ).fetchone()
                else:
                    existing = self._db.execute(
                        "SELECT payload_hash FROM data_catalog WHERE source=? AND instrument=? AND timeframe=? AND timestamp_ns=? AND sequence=?",
                        row[:5],
                    ).fetchone()
                if existing is not None:
                    if existing[0] != row[6]:
                        raise ValueError(f"conflicting historical record identity: {row[:5]}")
                    continue
                self._db.execute(
                    "INSERT INTO data_catalog(source,instrument,timeframe,timestamp_ns,sequence,payload_json,payload_hash,ingested_at_ns) VALUES(?,?,?,?,?,?,?,?)",
                    row,
                )
                inserted += 1
                self._db.execute(
                    "INSERT INTO source_watermarks(source,instrument,timeframe,max_timestamp_ns) VALUES(?,?,?,?) "
                    "ON CONFLICT(source,instrument,timeframe) DO UPDATE SET max_timestamp_ns=MAX(source_watermarks.max_timestamp_ns, excluded.max_timestamp_ns)",
                    (row[0], row[1], row[2], row[3]),
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return inserted

    def append(self, records: Iterable[HistoricalRecord], *, ingested_at_ns: int = 0) -> int:
        """Explicit append/upcoming-data API; preserves all earlier data and repairs gaps."""
        return self.ingest(records, ingested_at_ns=ingested_at_ns)

    def records(self, *, source: str, instrument: str, timeframe: str) -> tuple[HistoricalRecord, ...]:
        rows = self._db.execute(
            "SELECT source,instrument,timeframe,timestamp_ns,payload_json,sequence FROM data_catalog WHERE source=? AND instrument=? AND timeframe=? ORDER BY timestamp_ns, sequence",
            (source, instrument, timeframe),
        ).fetchall()
        return tuple(HistoricalRecord(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5]) for r in rows)

    def timestamps(self, *, source: str, instrument: str, timeframe: str, start_ns: int, end_ns: int) -> tuple[int, ...]:
        """Return distinct stored timestamps in an inclusive range."""
        if start_ns < 0 or end_ns < start_ns:
            raise ValueError("invalid timestamp range")
        rows = self._db.execute(
            "SELECT DISTINCT timestamp_ns FROM data_catalog WHERE source=? AND instrument=? AND timeframe=? AND timestamp_ns BETWEEN ? AND ? ORDER BY timestamp_ns",
            (source, instrument, timeframe, start_ns, end_ns),
        ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def watermark(self, *, source: str, instrument: str, timeframe: str) -> int | None:
        row = self._db.execute(
            "SELECT max_timestamp_ns FROM source_watermarks WHERE source=? AND instrument=? AND timeframe=?",
            (source, instrument, timeframe),
        ).fetchone()
        return None if row is None else int(row[0])

    def gaps(self, *, source: str, instrument: str, timeframe: str, interval_ns: int) -> tuple[Gap, ...]:
        """Return missing cadence ranges between observed timestamps for targeted repair."""
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        rows = self._db.execute(
            "SELECT DISTINCT timestamp_ns FROM data_catalog WHERE source=? AND instrument=? AND timeframe=? ORDER BY timestamp_ns",
            (source, instrument, timeframe),
        ).fetchall()
        timestamps = [int(r[0]) for r in rows]
        gaps: list[Gap] = []
        for previous, current in zip(timestamps, timestamps[1:]):
            if current - previous > interval_ns:
                gaps.append(Gap(instrument, timeframe, previous + interval_ns, current - interval_ns))
        return tuple(gaps)

    def count(
        self,
        *,
        source: str | None = None,
        instrument: str | None = None,
        timeframe: str | None = None,
    ) -> int:
        clauses, params = [], []
        if source is not None:
            clauses.append("source=?")
            params.append(source)
        if instrument is not None:
            clauses.append("instrument=?")
            params.append(instrument)
        if timeframe is not None:
            clauses.append("timeframe=?")
            params.append(timeframe)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return int(self._db.execute("SELECT COUNT(*) FROM data_catalog" + where, params).fetchone()[0])
