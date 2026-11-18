"""Persistent idempotency for high-resolution replay events."""
from __future__ import annotations

import sqlite3

_LEGACY_INSTRUMENT = "__legacy__"
_LEGACY_EVENT_TYPE = "event"


class EventDedupStore:
    """Tracks the full durable identity of processed replay events."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(processed_replay_events)")}
        if columns and {"instrument", "event_type"}.issubset(columns):
            return
        if columns:
            self.connection.execute("ALTER TABLE processed_replay_events RENAME TO processed_replay_events_legacy")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS processed_replay_events (
                run_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                instrument TEXT NOT NULL,
                event_type TEXT NOT NULL,
                PRIMARY KEY (run_id, timestamp_ns, sequence, instrument, event_type)
            )"""
        )
        if columns:
            self.connection.execute(
                """INSERT OR IGNORE INTO processed_replay_events
                   (run_id,timestamp_ns,sequence,instrument,event_type)
                   SELECT run_id,timestamp_ns,sequence,?,? FROM processed_replay_events_legacy""",
                (_LEGACY_INSTRUMENT, _LEGACY_EVENT_TYPE),
            )
        self.connection.commit()

    def mark_if_new(self, run_id: str, timestamp_ns: int, sequence: int, instrument: str = _LEGACY_INSTRUMENT, event_type: str = _LEGACY_EVENT_TYPE) -> bool:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < 0:
            raise ValueError("timestamp_ns must be a non-negative integer")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if not isinstance(instrument, str) or not instrument.strip() or not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("instrument and event_type are required")
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO processed_replay_events"
            "(run_id,timestamp_ns,sequence,instrument,event_type) VALUES(?,?,?,?,?)",
            (run_id, timestamp_ns, sequence, instrument.strip(), event_type.strip()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def contains(self, run_id: str, timestamp_ns: int, sequence: int, instrument: str = _LEGACY_INSTRUMENT, event_type: str = _LEGACY_EVENT_TYPE) -> bool:
        if not isinstance(instrument, str) or not instrument.strip() or not isinstance(event_type, str) or not event_type.strip():
            return False
        return self.connection.execute(
            "SELECT 1 FROM processed_replay_events WHERE run_id=? AND timestamp_ns=? "
            "AND sequence=? AND instrument=? AND event_type=?",
            (run_id, timestamp_ns, sequence, instrument.strip(), event_type.strip()),
        ).fetchone() is not None
