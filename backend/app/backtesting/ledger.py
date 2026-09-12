"""Durable SQLite ledger and checkpoint storage for backtest replay."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping


LEDGER_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class LedgerRecord:
    run_id: str
    record_type: str
    timestamp_ns: int
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    event_index: int
    timestamp_ns: int
    state: Mapping[str, Any]


class BacktestLedger:
    """Append-only durable replay ledger with resumable checkpoints."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                strategy_hash TEXT,
                initial_capital REAL NOT NULL,
                metadata_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 2,
                data_source_fingerprint TEXT
            )
        """)
        self._migrate_runs_metadata()
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id TEXT PRIMARY KEY,
                event_index INTEGER NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS checkpoint_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_index INTEGER NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                UNIQUE(run_id, event_index, timestamp_ns, state_json),
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
        """)
        self._db.execute("""
            INSERT OR IGNORE INTO checkpoint_history(run_id,event_index,timestamp_ns,state_json)
            SELECT run_id,event_index,timestamp_ns,state_json FROM checkpoints
        """)
        self._db.commit()

    def _migrate_runs_metadata(self) -> None:
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(runs)").fetchall()}
        if "schema_version" not in columns:
            self._db.execute(
                f"ALTER TABLE runs ADD COLUMN schema_version INTEGER NOT NULL DEFAULT {LEDGER_SCHEMA_VERSION}"
            )
        if "data_source_fingerprint" not in columns:
            self._db.execute("ALTER TABLE runs ADD COLUMN data_source_fingerprint TEXT")

    def close(self) -> None:
        self._db.close()

    def start_run(self, run_id: str, strategy_id: str, strategy_version: str,
                  initial_capital: float, *, strategy_hash: str | None = None,
                  metadata: Mapping[str, Any] | None = None,
                  schema_version: int = LEDGER_SCHEMA_VERSION,
                  data_source_fingerprint: str | None = None) -> None:
        if not run_id.strip() or not strategy_id.strip() or not strategy_version.strip():
            raise ValueError("run and strategy identifiers are required")
        if initial_capital < 0:
            raise ValueError("initial_capital cannot be negative")
        if schema_version <= 0:
            raise ValueError("schema_version must be positive")
        if data_source_fingerprint is not None and not data_source_fingerprint.strip():
            raise ValueError("data_source_fingerprint cannot be empty")
        try:
            self._db.execute(
                "INSERT INTO runs(run_id,strategy_id,strategy_version,strategy_hash,initial_capital,metadata_json,schema_version,data_source_fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, strategy_id, strategy_version, strategy_hash, float(initial_capital),
                 json.dumps(dict(metadata or {}), sort_keys=True), int(schema_version), data_source_fingerprint),
            )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise ValueError(f"run_id already exists: {run_id}") from exc

    def _require_run(self, run_id: str) -> None:
        if self._db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
            raise ValueError(f"unknown run_id: {run_id}")

    def validate_resume(self, run_id: str, *, schema_version: int = LEDGER_SCHEMA_VERSION,
                        data_source_fingerprint: str | None = None) -> None:
        row = self._db.execute(
            "SELECT schema_version,data_source_fingerprint FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown run_id: {run_id}")
        stored_schema, stored_fingerprint = int(row[0]), row[1]
        if stored_schema != int(schema_version):
            raise ValueError(
                f"unsafe resume: schema_version mismatch (stored={stored_schema}, requested={schema_version})"
            )
        if stored_fingerprint != data_source_fingerprint:
            raise ValueError(
                "unsafe resume: data_source_fingerprint mismatch "
                f"(stored={stored_fingerprint!r}, requested={data_source_fingerprint!r})"
            )

    @staticmethod
    def _record_params(record: LedgerRecord) -> tuple[str, str, int, str]:
        if record.timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if not record.run_id.strip() or not record.record_type.strip():
            raise ValueError("run_id and record_type are required")
        return (record.run_id, record.record_type, record.timestamp_ns,
                json.dumps(dict(record.payload), sort_keys=True, default=str))

    @staticmethod
    def _row_to_record(row: tuple[Any, ...]) -> LedgerRecord:
        return LedgerRecord(row[0], row[1], row[2], json.loads(row[3]))

    def append(self, record: LedgerRecord) -> None:
        self._require_run(record.run_id)
        self._db.execute(
            "INSERT INTO records(run_id,record_type,timestamp_ns,payload_json) VALUES(?,?,?,?)",
            self._record_params(record),
        )
        self._db.commit()

    def append_batch(self, records: Iterable[LedgerRecord]) -> int:
        """Append many records in one transaction and return the count."""
        rows = list(records)
        if not rows:
            return 0
        for record in rows:
            self._record_params(record)
        run_ids = {record.run_id for record in rows}
        for run_id in run_ids:
            self._require_run(run_id)
        try:
            self._db.executemany(
                "INSERT INTO records(run_id,record_type,timestamp_ns,payload_json) VALUES(?,?,?,?)",
                (self._record_params(record) for record in rows),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return len(rows)

    def checkpoint(self, checkpoint: Checkpoint) -> None:
        if checkpoint.event_index < 0 or checkpoint.timestamp_ns < 0:
            raise ValueError("checkpoint indexes and timestamps cannot be negative")
        self._require_run(checkpoint.run_id)
        state_json = json.dumps(dict(checkpoint.state), sort_keys=True, default=str)
        try:
            self._db.execute(
                "INSERT OR IGNORE INTO checkpoint_history(run_id,event_index,timestamp_ns,state_json) VALUES(?,?,?,?)",
                (checkpoint.run_id, checkpoint.event_index, checkpoint.timestamp_ns, state_json),
            )
            self._db.execute(
                "INSERT INTO checkpoints(run_id,event_index,timestamp_ns,state_json) VALUES(?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET event_index=excluded.event_index,timestamp_ns=excluded.timestamp_ns,state_json=excluded.state_json",
                (checkpoint.run_id, checkpoint.event_index, checkpoint.timestamp_ns, state_json),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def load_checkpoint(self, run_id: str) -> Checkpoint | None:
        row = self._db.execute(
            "SELECT run_id,event_index,timestamp_ns,state_json FROM checkpoints WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return Checkpoint(row[0], row[1], row[2], json.loads(row[3]))

    def checkpoint_history(self, run_id: str) -> tuple[Checkpoint, ...]:
        """Return all durable checkpoints in write order; latest remains available via load_checkpoint()."""
        rows = self._db.execute(
            "SELECT run_id,event_index,timestamp_ns,state_json FROM checkpoint_history WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        return tuple(Checkpoint(r[0], r[1], r[2], json.loads(r[3])) for r in rows)

    def iter_records(self, run_id: str, record_type: str | None = None,
                     *, fetch_size: int = 256) -> Iterator[LedgerRecord]:
        """Stream durable records without materializing the complete result set."""
        if fetch_size <= 0:
            raise ValueError("fetch_size must be positive")
        self._require_run(run_id)
        if record_type is None:
            cursor = self._db.execute(
                "SELECT run_id,record_type,timestamp_ns,payload_json FROM records WHERE run_id=? ORDER BY id",
                (run_id,),
            )
        else:
            cursor = self._db.execute(
                "SELECT run_id,record_type,timestamp_ns,payload_json FROM records WHERE run_id=? AND record_type=? ORDER BY id",
                (run_id, record_type),
            )
        while True:
            rows = cursor.fetchmany(fetch_size)
            if not rows:
                break
            for row in rows:
                yield self._row_to_record(row)

    def record_count(self, run_id: str, record_type: str | None = None) -> int:
        """Return a durable record count without loading record payloads."""
        self._require_run(run_id)
        if record_type is None:
            row = self._db.execute("SELECT COUNT(*) FROM records WHERE run_id=?", (run_id,)).fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) FROM records WHERE run_id=? AND record_type=?", (run_id, record_type)
            ).fetchone()
        return int(row[0])

    def record_at(self, run_id: str, record_type: str | None, index: int) -> LedgerRecord:
        """Read one durable record by zero-based result index."""
        self._require_run(run_id)
        if index < 0:
            count = self.record_count(run_id, record_type)
            index += count
        if index < 0:
            raise IndexError("record index out of range")
        if record_type is None:
            row = self._db.execute(
                "SELECT run_id,record_type,timestamp_ns,payload_json FROM records WHERE run_id=? ORDER BY id LIMIT 1 OFFSET ?",
                (run_id, index),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT run_id,record_type,timestamp_ns,payload_json FROM records WHERE run_id=? AND record_type=? ORDER BY id LIMIT 1 OFFSET ?",
                (run_id, record_type, index),
            ).fetchone()
        if row is None:
            raise IndexError("record index out of range")
        return self._row_to_record(row)

    def records(self, run_id: str, record_type: str | None = None) -> tuple[LedgerRecord, ...]:
        """Compatibility API that materializes all matching records."""
        return tuple(self.iter_records(run_id, record_type))

    def run_metadata(self, run_id: str) -> Mapping[str, Any] | None:
        row = self._db.execute(
            "SELECT strategy_id,strategy_version,strategy_hash,initial_capital,metadata_json,schema_version,data_source_fingerprint FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return {"strategy_id": row[0], "strategy_version": row[1], "strategy_hash": row[2],
                "initial_capital": row[3], "metadata": json.loads(row[4]),
                "schema_version": row[5], "data_source_fingerprint": row[6]}
