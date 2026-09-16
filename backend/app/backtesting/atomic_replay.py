"""Single-transaction durable state for restartable high-resolution replay."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict
from typing import Any

from app.backtesting.checkpoint import ReplayCheckpoint
from app.backtesting.high_resolution_pnl import HighResolutionTrade

_LEGACY_INSTRUMENT = "__legacy__"
_LEGACY_EVENT_TYPE = "event"


class AtomicReplayStore:
    """Keeps event idempotency, checkpoint, and completed trades in one DB transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS atomic_replay_events (
                run_id TEXT NOT NULL, timestamp_ns INTEGER NOT NULL, sequence INTEGER NOT NULL,
                instrument TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (run_id, timestamp_ns, sequence, instrument, event_type)
            );
            CREATE TABLE IF NOT EXISTS atomic_replay_checkpoints (
                run_id TEXT PRIMARY KEY, timestamp_ns INTEGER NOT NULL, sequence INTEGER NOT NULL,
                processed_events INTEGER NOT NULL, realized_pnl REAL NOT NULL, state_json TEXT NOT NULL,
                instrument TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS atomic_replay_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, instrument TEXT NOT NULL,
                quantity INTEGER NOT NULL, entry_timestamp_ns INTEGER NOT NULL, exit_timestamp_ns INTEGER NOT NULL,
                entry_price REAL NOT NULL, exit_price REAL NOT NULL, gross_pnl REAL NOT NULL,
                fees REAL NOT NULL, net_pnl REAL NOT NULL
            );
        """)
        event_columns = {row[1] for row in self._db.execute("PRAGMA table_info(atomic_replay_events)")}
        if event_columns and "instrument" not in event_columns:
            self._db.execute("ALTER TABLE atomic_replay_events RENAME TO atomic_replay_events_legacy")
            self._db.execute("""CREATE TABLE atomic_replay_events (
                run_id TEXT NOT NULL, timestamp_ns INTEGER NOT NULL, sequence INTEGER NOT NULL,
                instrument TEXT NOT NULL, event_type TEXT NOT NULL,
                PRIMARY KEY (run_id, timestamp_ns, sequence, instrument, event_type)
            )""")
            self._db.execute("""INSERT OR IGNORE INTO atomic_replay_events
                SELECT run_id,timestamp_ns,sequence,?,? FROM atomic_replay_events_legacy""",
                (_LEGACY_INSTRUMENT, _LEGACY_EVENT_TYPE))
        checkpoint_columns = {row[1] for row in self._db.execute("PRAGMA table_info(atomic_replay_checkpoints)")}
        if checkpoint_columns and "instrument" not in checkpoint_columns:
            self._db.execute("ALTER TABLE atomic_replay_checkpoints ADD COLUMN instrument TEXT NOT NULL DEFAULT ''")
        if checkpoint_columns and "event_type" not in checkpoint_columns:
            self._db.execute("ALTER TABLE atomic_replay_checkpoints ADD COLUMN event_type TEXT NOT NULL DEFAULT ''")
        self._db.commit()

    def begin(self) -> None:
        if self._db.in_transaction:
            raise RuntimeError("atomic replay transaction already active")
        self._db.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self._db.commit()

    def rollback(self) -> None:
        if self._db.in_transaction:
            self._db.rollback()

    def mark_if_new(self, run_id: str, timestamp_ns: int, sequence: int, instrument: str = _LEGACY_INSTRUMENT, event_type: str = _LEGACY_EVENT_TYPE) -> bool:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("sequence must be non-negative")
        if not isinstance(instrument, str) or not instrument.strip() or not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("instrument and event_type are required")
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO atomic_replay_events(run_id,timestamp_ns,sequence,instrument,event_type) VALUES(?,?,?,?,?)",
            (run_id, timestamp_ns, sequence, instrument.strip(), event_type.strip()),
        )
        return cursor.rowcount == 1

    def load_checkpoint(self, run_id: str) -> ReplayCheckpoint | None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        row = self._db.execute(
            "SELECT run_id,timestamp_ns,sequence,processed_events,realized_pnl,state_json,instrument,event_type FROM atomic_replay_checkpoints WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        state = json.loads(row[5])
        return ReplayCheckpoint(row[0], row[1], row[2], row[3], row[4], state, row[6], row[7])

    def save_checkpoint(self, checkpoint: ReplayCheckpoint) -> None:
        if not isinstance(checkpoint.run_id, str) or not checkpoint.run_id.strip():
            raise ValueError("run_id is required")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (checkpoint.timestamp_ns, checkpoint.sequence, checkpoint.processed_events)):
            raise ValueError("checkpoint integer fields are invalid")
        if isinstance(checkpoint.realized_pnl, bool) or not isinstance(checkpoint.realized_pnl, (int, float)) or not math.isfinite(float(checkpoint.realized_pnl)):
            raise ValueError("checkpoint realized_pnl must be finite")
        if not isinstance(checkpoint.state, dict):
            raise ValueError("checkpoint state must be a dictionary")
        existing = self._db.execute(
            "SELECT timestamp_ns,sequence,processed_events,instrument,event_type FROM atomic_replay_checkpoints WHERE run_id=?",
            (checkpoint.run_id,),
        ).fetchone()
        new_key = (checkpoint.timestamp_ns, checkpoint.sequence, checkpoint.instrument, checkpoint.event_type)
        if existing is not None:
            old_key = (existing[0], existing[1], existing[3], existing[4])
            if new_key < old_key or checkpoint.processed_events < existing[2]:
                raise ValueError("checkpoint cannot move backwards")
        self._db.execute(
            """INSERT INTO atomic_replay_checkpoints
            (run_id,timestamp_ns,sequence,processed_events,realized_pnl,state_json,instrument,event_type)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET
            timestamp_ns=excluded.timestamp_ns, sequence=excluded.sequence,
            processed_events=excluded.processed_events, realized_pnl=excluded.realized_pnl,
            state_json=excluded.state_json, instrument=excluded.instrument, event_type=excluded.event_type""",
            (checkpoint.run_id, checkpoint.timestamp_ns, checkpoint.sequence, checkpoint.processed_events,
             float(checkpoint.realized_pnl), json.dumps(checkpoint.state, sort_keys=True, separators=(",", ":")),
             checkpoint.instrument, checkpoint.event_type),
        )

    def append_trade(self, run_id: str, trade: HighResolutionTrade) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if not isinstance(trade, HighResolutionTrade):
            raise TypeError("trade must be HighResolutionTrade")
        payload = asdict(trade)
        self._db.execute(
            "INSERT INTO atomic_replay_trades "
            "(run_id,instrument,quantity,entry_timestamp_ns,exit_timestamp_ns,entry_price,exit_price,gross_pnl,fees,net_pnl) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, payload["instrument"], payload["quantity"], payload["entry_timestamp_ns"], payload["exit_timestamp_ns"],
             payload["entry_price"], payload["exit_price"], payload["gross_pnl"], payload["fees"], trade.net_pnl),
        )

    def trades(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT instrument,quantity,entry_timestamp_ns,exit_timestamp_ns,entry_price,exit_price,gross_pnl,fees,net_pnl FROM atomic_replay_trades WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        fields = ("instrument", "quantity", "entry_timestamp_ns", "exit_timestamp_ns", "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl")
        return [dict(zip(fields, row)) for row in rows]

    def count_events(self, run_id: str) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM atomic_replay_events WHERE run_id=?", (run_id,)).fetchone()[0])

    def count_trades(self, run_id: str) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM atomic_replay_trades WHERE run_id=?", (run_id,)).fetchone()[0])
