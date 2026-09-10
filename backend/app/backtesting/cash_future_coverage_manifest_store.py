"""Durable SQLite coverage-manifest persistence for Cash-Future history."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .cash_future_coverage_manifest import CoverageManifest, CoverageRange


class CashFutureCoverageManifestStore:
    """Persist coverage ranges incrementally and idempotently in SQLite."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS cash_future_coverage_manifest (
                    source TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    start_ns INTEGER NOT NULL,
                    end_ns INTEGER NOT NULL,
                    expected_points INTEGER NOT NULL,
                    observed_points INTEGER NOT NULL,
                    missing_points INTEGER NOT NULL,
                    complete INTEGER NOT NULL,
                    generated_at_ns INTEGER NOT NULL,
                    PRIMARY KEY (source, instrument, start_ns, end_ns)
                )"""
            )

    def upsert(self, manifest: CoverageManifest) -> None:
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO cash_future_coverage_manifest
                (source,instrument,start_ns,end_ns,expected_points,observed_points,
                 missing_points,complete,generated_at_ns)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source,instrument,start_ns,end_ns) DO UPDATE SET
                    expected_points=excluded.expected_points,
                    observed_points=excluded.observed_points,
                    missing_points=excluded.missing_points,
                    complete=excluded.complete,
                    generated_at_ns=excluded.generated_at_ns""",
                [
                    (
                        manifest.source,
                        item.instrument,
                        item.start_ns,
                        item.end_ns,
                        item.expected_points,
                        item.observed_points,
                        item.missing_points,
                        int(item.complete),
                        manifest.generated_at_ns,
                    )
                    for item in manifest.ranges
                ],
            )

    def ranges(self, *, source: str, instrument: str | None = None) -> tuple[CoverageRange, ...]:
        query = "SELECT instrument,start_ns,end_ns,expected_points,observed_points,missing_points,complete FROM cash_future_coverage_manifest WHERE source=?"
        args: list[object] = [source]
        if instrument is not None:
            query += " AND instrument=?"
            args.append(instrument)
        query += " ORDER BY instrument,start_ns,end_ns"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return tuple(CoverageRange(*row) for row in rows)

    def missing(self, *, source: str) -> tuple[CoverageRange, ...]:
        return tuple(item for item in self.ranges(source=source) if item.missing_points)


__all__ = ["CashFutureCoverageManifestStore"]
