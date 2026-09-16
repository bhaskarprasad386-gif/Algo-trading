"""Crash-safe checkpoints for streaming high-resolution backtests."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
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

    @staticmethod
    def _validate(checkpoint: ReplayCheckpoint) -> None:
        if not isinstance(checkpoint.run_id, str) or not checkpoint.run_id.strip():
            raise ValueError("run_id is required")
        for name, value in (("timestamp_ns", checkpoint.timestamp_ns), ("sequence", checkpoint.sequence), ("processed_events", checkpoint.processed_events)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if isinstance(checkpoint.realized_pnl, bool) or not isinstance(checkpoint.realized_pnl, (int, float)) or not math.isfinite(float(checkpoint.realized_pnl)):
            raise ValueError("realized_pnl must be finite")
        if not isinstance(checkpoint.state, dict):
            raise ValueError("checkpoint state must be a dictionary")
        try:
            json.dumps(checkpoint.state, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint state must be JSON serializable") from exc

    def save(self, checkpoint: ReplayCheckpoint) -> None:
        self._validate(checkpoint)
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
             checkpoint.processed_events, float(checkpoint.realized_pnl),
             json.dumps(checkpoint.state, sort_keys=True, separators=(",", ":"))),
        )
        self.connection.commit()

    def load(self, run_id: str) -> ReplayCheckpoint | None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        row = self.connection.execute(
            "SELECT run_id,timestamp_ns,sequence,processed_events,realized_pnl,state_json "
            "FROM backtest_checkpoints WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            state = json.loads(row[5])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("checkpoint state is invalid JSON") from exc
        checkpoint = ReplayCheckpoint(row[0], row[1], row[2], row[3], row[4], state)
        self._validate(checkpoint)
        return checkpoint

    def clear(self, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        self.connection.execute("DELETE FROM backtest_checkpoints WHERE run_id=?", (run_id,))
        self.connection.commit()
