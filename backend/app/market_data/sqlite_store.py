import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredSnapshot:
    key: str
    payload: str


class SQLiteSnapshotStore:
    """Minimal persistent snapshot store; safe replacement for in-memory storage."""

    def __init__(self, path: str | Path = "market_data.db") -> None:
        self.path = str(path)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS snapshots ("
                "key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def put(self, key: str, payload: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO snapshots(key, payload) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                (key, payload),
            )

    def get(self, key: str) -> StoredSnapshot | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT key, payload FROM snapshots WHERE key = ?", (key,)
            ).fetchone()
        return StoredSnapshot(*row) if row else None
