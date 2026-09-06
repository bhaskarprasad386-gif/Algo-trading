"""Durable SQLite ledger and checkpoint storage for backtest replay."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


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
                metadata_json TEXT NOT NULL
            )
        """)
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
        # Preserve a latest checkpoint created by an older schema during upgrade.
        self._db.execute("""
            INSERT OR IGNORE INTO checkpoint_history(run_id,event_index,timestamp_ns,state_json)
            SELECT run_id,event_index,timestamp_ns,state_json FROM checkpoints
        """)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def start_run(self, run_id: str, strategy_id: str, strategy_version: str,
                  initial_capital: float, *, strategy_hash: str | None = None,
                  metadata: Mapping[str, Any] | None = None) -> None:
        if not run_id.strip() or not strategy_id.strip() or not strategy_version.strip():
            raise ValueError("run and strategy identifiers are required")
        if initial_capital < 0:
            raise ValueError("initial_capital cannot be negative")
        try:
            self._db.execute(
                "INSERT INTO runs(run_id,strategy_id,strategy_version,strategy_hash,initial_capital,metadata_json) VALUES(?,?,?,?,?,?)",
                (run_id, strategy_id, strategy_version, strategy_hash, float(initial_capital), json.dumps(dict(metadata or {}), sort_keys=True)),
            )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise ValueError(f"run_id already exists: {run_id}") from exc

    def _require_run(self, run_id: str) -> None:
        if self._db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
            raise ValueError(f"unknown run_id: {run_id}")

    @staticmethod
    def _record_params(record: LedgerRecord) -> tuple[str, str, int, str]:
        if record.timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if not record.run_id.strip() or not record.record_type.strip():
            raise ValueError("run_id and record_type are required")
        return (record.run_id, record.record_type, record.timestamp_ns,
                json.dumps(dict(record.payload), sort_keys=True, default=str))

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

    def records(self, run_id: str, record_type: str | None = None) -> tuple[LedgerRecord, ...]:
        if record_type is None:
            rows = self._db.execute(
                "SELECT run_id,record_type,timestamp_ns,payload_json FROM records WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT run_id,record_type,timestamp_ns,payload_json FROM records WHERE run_id=? AND record_type=? ORDER BY id",
                (run_id, record_type),
            ).fetchall()
        return tuple(LedgerRecord(r[0], r[1], r[2], json.loads(r[3])) for r in rows)

    def run_metadata(self, run_id: str) -> Mapping[str, Any] | None:
        row = self._db.execute(
            "SELECT strategy_id,strategy_version,strategy_hash,initial_capital,metadata_json FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return {"strategy_id": row[0], "strategy_version": row[1], "strategy_hash": row[2],
                "initial_capital": row[3], "metadata": json.loads(row[4])}
