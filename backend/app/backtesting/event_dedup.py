"""Persistent idempotency for high-resolution replay events."""
from __future__ import annotations

import sqlite3


class EventDedupStore:
    """Tracks processed (run, timestamp_ns, sequence) identities durably."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS processed_replay_events (
                run_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                PRIMARY KEY (run_id, timestamp_ns, sequence)
            )"""
        )
        self.connection.commit()

    def mark_if_new(self, run_id: str, timestamp_ns: int, sequence: int) -> bool:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if timestamp_ns < 0 or sequence < 0:
            raise ValueError("event identity values must be non-negative")
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO processed_replay_events(run_id,timestamp_ns,sequence) VALUES(?,?,?)",
            (run_id, timestamp_ns, sequence),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def contains(self, run_id: str, timestamp_ns: int, sequence: int) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM processed_replay_events WHERE run_id=? AND timestamp_ns=? AND sequence=?",
            (run_id, timestamp_ns, sequence),
        ).fetchone() is not None
