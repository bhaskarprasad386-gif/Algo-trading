"""Single-transaction durable state for restartable high-resolution replay."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from app.backtesting.checkpoint import ReplayCheckpoint
from app.backtesting.high_resolution_pnl import HighResolutionTrade


class AtomicReplayStore:
    """Keeps event idempotency, checkpoint, and completed trades in one DB transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS atomic_replay_events (
                run_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                PRIMARY KEY (run_id, timestamp_ns, sequence)
            );
            CREATE TABLE IF NOT EXISTS atomic_replay_checkpoints (
                run_id TEXT PRIMARY KEY,
                timestamp_ns INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                processed_events INTEGER NOT NULL,
                realized_pnl REAL NOT NULL,
                state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atomic_replay_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                instrument TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_timestamp_ns INTEGER NOT NULL,
                exit_timestamp_ns INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                gross_pnl REAL NOT NULL,
                fees REAL NOT NULL,
                net_pnl REAL NOT NULL
            );
            """
        )
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

    def mark_if_new(self, run_id: str, timestamp_ns: int, sequence: int) -> bool:
        if not run_id or timestamp_ns < 0 or sequence < 0:
            raise ValueError("invalid replay event identity")
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO atomic_replay_events(run_id,timestamp_ns,sequence) VALUES(?,?,?)",
            (run_id, timestamp_ns, sequence),
        )
        return cursor.rowcount == 1

    def load_checkpoint(self, run_id: str) -> ReplayCheckpoint | None:
        row = self._db.execute(
            "SELECT run_id,timestamp_ns,sequence,processed_events,realized_pnl,state_json "
            "FROM atomic_replay_checkpoints WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return ReplayCheckpoint(row[0], row[1], row[2], row[3], row[4], json.loads(row[5]))

    def save_checkpoint(self, checkpoint: ReplayCheckpoint) -> None:
        if not checkpoint.run_id or checkpoint.timestamp_ns < 0 or checkpoint.sequence < 0:
            raise ValueError("invalid checkpoint")
        if checkpoint.processed_events < 0:
            raise ValueError("processed_events must be non-negative")
        existing = self._db.execute(
            "SELECT timestamp_ns,sequence,processed_events FROM atomic_replay_checkpoints WHERE run_id=?",
            (checkpoint.run_id,),
        ).fetchone()
        new_key = (checkpoint.timestamp_ns, checkpoint.sequence)
        if existing is not None:
            old_key = (existing[0], existing[1])
            if new_key < old_key or checkpoint.processed_events < existing[2]:
                raise ValueError("checkpoint cannot move backwards")
        self._db.execute(
            "INSERT INTO atomic_replay_checkpoints "
            "(run_id,timestamp_ns,sequence,processed_events,realized_pnl,state_json) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET timestamp_ns=excluded.timestamp_ns, "
            "sequence=excluded.sequence,processed_events=excluded.processed_events, "
            "realized_pnl=excluded.realized_pnl,state_json=excluded.state_json",
            (checkpoint.run_id, checkpoint.timestamp_ns, checkpoint.sequence,
             checkpoint.processed_events, checkpoint.realized_pnl, json.dumps(checkpoint.state, sort_keys=True)),
        )

    def append_trade(self, run_id: str, trade: HighResolutionTrade) -> None:
        payload = asdict(trade)
        self._db.execute(
            "INSERT INTO atomic_replay_trades "
            "(run_id,instrument,quantity,entry_timestamp_ns,exit_timestamp_ns,entry_price,exit_price,gross_pnl,fees,net_pnl) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, payload["instrument"], payload["quantity"], payload["entry_timestamp_ns"],
             payload["exit_timestamp_ns"], payload["entry_price"], payload["exit_price"],
             payload["gross_pnl"], payload["fees"], trade.net_pnl),
        )

    def trades(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT instrument,quantity,entry_timestamp_ns,exit_timestamp_ns,entry_price,exit_price,gross_pnl,fees,net_pnl "
            "FROM atomic_replay_trades WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        fields = ("instrument", "quantity", "entry_timestamp_ns", "exit_timestamp_ns",
                  "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl")
        return [dict(zip(fields, row)) for row in rows]

    def count_events(self, run_id: str) -> int:
        return int(self._db.execute(
            "SELECT COUNT(*) FROM atomic_replay_events WHERE run_id=?", (run_id,)
        ).fetchone()[0])

    def count_trades(self, run_id: str) -> int:
        return int(self._db.execute(
            "SELECT COUNT(*) FROM atomic_replay_trades WHERE run_id=?", (run_id,)
        ).fetchone()[0])
