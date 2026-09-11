"""Durable SQLite coverage-manifest persistence for Cash-Future history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .cash_future_coverage_manifest import CoverageManifest, CoverageRange


class CashFutureCoverageManifestStore:
    """Persist coverage ranges incrementally and idempotently in SQLite."""

    _TABLE = "cash_future_coverage_manifest"

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @classmethod
    def _create_table(cls, conn: sqlite3.Connection, table: str | None = None) -> None:
        table = table or cls._TABLE
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {table} (
                source TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                instrument TEXT NOT NULL,
                start_ns INTEGER NOT NULL,
                end_ns INTEGER NOT NULL,
                expected_points INTEGER NOT NULL,
                observed_points INTEGER NOT NULL,
                missing_points INTEGER NOT NULL,
                complete INTEGER NOT NULL,
                generated_at_ns INTEGER NOT NULL,
                PRIMARY KEY (source, timeframe, instrument, start_ns, end_ns)
            )"""
        )

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (self._TABLE,),
            ).fetchone()
            if not exists:
                self._create_table(conn)
                return

            columns = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({self._TABLE})").fetchall()
            }
            if "timeframe" not in columns:
                legacy = f"{self._TABLE}_legacy"
                conn.execute(f"ALTER TABLE {self._TABLE} RENAME TO {legacy}")
                self._create_table(conn)
                conn.execute(
                    f"""INSERT INTO {self._TABLE}
                    (source,timeframe,instrument,start_ns,end_ns,expected_points,
                     observed_points,missing_points,complete,generated_at_ns)
                    SELECT source,'1m',instrument,start_ns,end_ns,expected_points,
                           observed_points,missing_points,complete,generated_at_ns
                    FROM {legacy}"""
                )
                conn.execute(f"DROP TABLE {legacy}")

    def upsert(self, manifest: CoverageManifest, *, timeframe: str = "1m") -> None:
        if not timeframe:
            raise ValueError("timeframe must not be empty")
        with self._connect() as conn:
            conn.executemany(
                f"""INSERT INTO {self._TABLE}
                (source,timeframe,instrument,start_ns,end_ns,expected_points,observed_points,
                 missing_points,complete,generated_at_ns)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source,timeframe,instrument,start_ns,end_ns) DO UPDATE SET
                    expected_points=excluded.expected_points,
                    observed_points=excluded.observed_points,
                    missing_points=excluded.missing_points,
                    complete=excluded.complete,
                    generated_at_ns=excluded.generated_at_ns""",
                [
                    (
                        manifest.source,
                        timeframe,
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

    def ranges(
        self,
        *,
        source: str,
        timeframe: str = "1m",
        instrument: str | None = None,
    ) -> tuple[CoverageRange, ...]:
        query = (
            f"SELECT instrument,start_ns,end_ns,expected_points,observed_points,"
            f"missing_points,complete FROM {self._TABLE} WHERE source=? AND timeframe=?"
        )
        args: list[object] = [source, timeframe]
        if instrument is not None:
            query += " AND instrument=?"
            args.append(instrument)
        query += " ORDER BY instrument,start_ns,end_ns"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return tuple(CoverageRange(*row) for row in rows)

    def missing(
        self,
        *,
        source: str,
        timeframe: str = "1m",
        instrument: str | None = None,
    ) -> tuple[CoverageRange, ...]:
        """Return only incomplete persisted ranges for targeted repair."""
        return tuple(
            item
            for item in self.ranges(source=source, timeframe=timeframe, instrument=instrument)
            if item.missing_points > 0 or not item.complete
        )

    def repair_plan(
        self,
        *,
        source: str,
        timeframe: str = "1m",
        instrument: str | None = None,
    ) -> tuple[tuple[str, int, int], ...]:
        """Return durable repair work as ``(instrument,start_ns,end_ns)`` tuples.

        This is deliberately read-only. Acquisition consumes this bounded plan
        and repairs only incomplete ranges; successful acquisition updates the
        same rows through ``upsert``.
        """
        return tuple(
            (item.instrument, item.start_ns, item.end_ns)
            for item in self.missing(
                source=source,
                timeframe=timeframe,
                instrument=instrument,
            )
        )

    def is_complete_for_instruments(
        self,
        *,
        source: str,
        timeframe: str = "1m",
        instruments: tuple[str, ...] | list[str] | set[str],
    ) -> bool:
        """Return true only when every requested instrument has complete ranges.

        An absent instrument is deliberately incomplete. This makes the
        manifest an authoritative gate rather than allowing a partial manifest
        to masquerade as a complete universe.
        """
        requested = {instrument for instrument in instruments if instrument}
        if not requested:
            return False
        rows = self.ranges(source=source, timeframe=timeframe)
        by_instrument: dict[str, list[CoverageRange]] = {instrument: [] for instrument in requested}
        for item in rows:
            if item.instrument in by_instrument:
                by_instrument[item.instrument].append(item)
        return all(
            ranges and all(item.complete and item.missing_points == 0 for item in ranges)
            for ranges in by_instrument.values()
        )

    def is_complete_for_requests(
        self,
        *,
        source: str,
        timeframe: str = "1m",
        requests: tuple[tuple[str, int, int], ...] | list[tuple[str, int, int]],
    ) -> bool:
        """Return true only when complete manifest ranges cover every request interval.

        Instrument presence alone is insufficient: a complete range for the
        wrong historical period must not make a requested backtest ready.
        Coverage ranges may overlap or be split into adjacent chunks; together
        they must fully cover each requested interval without a gap.
        """
        if not requests:
            return False
        requested = tuple(requests)
        ranges_by_instrument: dict[str, list[tuple[int, int]]] = {}
        for item in self.ranges(source=source, timeframe=timeframe):
            if item.complete and item.missing_points == 0:
                ranges_by_instrument.setdefault(item.instrument, []).append(
                    (item.start_ns, item.end_ns)
                )

        for instrument, start_ns, end_ns in requested:
            if not instrument or end_ns < start_ns:
                return False
            covered_until = start_ns
            intervals = sorted(ranges_by_instrument.get(instrument, ()))
            for range_start, range_end in intervals:
                if range_end < covered_until:
                    continue
                if range_start > covered_until:
                    break
                covered_until = max(covered_until, range_end)
                if covered_until >= end_ns:
                    break
            if covered_until < end_ns:
                return False
        return True


__all__ = ["CashFutureCoverageManifestStore"]
