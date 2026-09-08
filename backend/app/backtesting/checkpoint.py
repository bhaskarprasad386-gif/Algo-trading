"""Crash-safe checkpoints for streaming high-resolution backtests."""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any


@dataclass(frozen=True)
class ReplayCheckpoint:
    run_id: str
    timestamp_ns: int
    sequence: int
    processed_events: int
    realized_pnl: float
    state: dict[str, Any]


class CheckpointStore:
    """SQLite-backed idempotent checkpoint store; only the latest state is retained."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS backtest_checkpoints (
                run_id TEXT PRIMARY KEY,
                timestamp_ns INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                processed_events INTEGER NOT NULL,
                realized_pnl REAL NOT NULL,
                state_json TEXT NOT NULL
            )"""
        )
        self.connection.commit()

    def save(self, checkpoint: ReplayCheckpoint) -> None:
        if not checkpoint.run_id.strip():
            raise ValueError("run_id is required")
        if checkpoint.timestamp_ns < 0 or checkpoint.sequence < 0:
            raise ValueError("checkpoint ordering values must be non-negative")
        if checkpoint.processed_events < 0:
            raise ValueError("processed_events must be non-negative")
        self.connection.execute(
            """INSERT INTO backtest_checkpoints
               (run_id,timestamp_ns,sequence,processed_events,realized_pnl,state_json)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET
                 timestamp_ns=excluded.timestamp_ns,
                 sequence=excluded.sequence,
                 processed_events=excluded.processed_events,
                 realized_pnl=excluded.realized_pnl,
                 state_json=excluded.state_json""",
            (checkpoint.run_id, checkpoint.timestamp_ns, checkpoint.sequence,
             checkpoint.processed_events, checkpoint.realized_pnl,
             json.dumps(checkpoint.state, sort_keys=True, separators=(",", ":"))),
        )
        self.connection.commit()

    def load(self, run_id: str) -> ReplayCheckpoint | None:
        row = self.connection.execute(
            "SELECT run_id,timestamp_ns,sequence,processed_events,realized_pnl,state_json "
            "FROM backtest_checkpoints WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return ReplayCheckpoint(row[0], row[1], row[2], row[3], row[4], json.loads(row[5]))

    def clear(self, run_id: str) -> None:
        self.connection.execute("DELETE FROM backtest_checkpoints WHERE run_id=?", (run_id,))
        self.connection.commit()
