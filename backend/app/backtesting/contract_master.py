"""Historical derivative contract master with snapshot-aware resolution."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable


@dataclass(frozen=True)
class ContractRecord:
    exchange: str
    symbol: str
    token: str
    expiry: date
    instrument_type: str
    underlying: str
    lot_size: int
    snapshot_date: date | None = None

    def __post_init__(self) -> None:
        for value, name in ((self.exchange, "exchange"), (self.symbol, "symbol"), (self.token, "token"), (self.instrument_type, "instrument_type"), (self.underlying, "underlying")):
            if not str(value).strip():
                raise ValueError(f"{name} is required")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")


class ContractMasterCatalog:
    """Durable SQLite cache retaining every contract-master snapshot."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS contract_master_snapshots (
            snapshot_date TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, payload_sha256 TEXT
        )""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS derivative_contracts (
            snapshot_date TEXT NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL,
            token TEXT NOT NULL, expiry TEXT NOT NULL, instrument_type TEXT NOT NULL,
            underlying TEXT NOT NULL, lot_size INTEGER NOT NULL,
            PRIMARY KEY(snapshot_date, exchange, token),
            FOREIGN KEY(snapshot_date) REFERENCES contract_master_snapshots(snapshot_date)
        )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_contract_lookup ON derivative_contracts(exchange, underlying, instrument_type, snapshot_date, expiry)")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def upsert_snapshot(self, snapshot_date: date, records: Iterable[ContractRecord], *, payload_sha256: str | None = None, fetched_at: datetime | None = None) -> int:
        rows = [(snapshot_date.isoformat(), r.exchange, r.symbol, r.token, r.expiry.isoformat(), r.instrument_type, r.underlying, r.lot_size) for r in records]
        now = (fetched_at or datetime.utcnow()).isoformat(timespec="seconds")
        with self._db:
            self._db.execute("INSERT INTO contract_master_snapshots(snapshot_date,fetched_at,payload_sha256) VALUES(?,?,?) ON CONFLICT(snapshot_date) DO UPDATE SET fetched_at=excluded.fetched_at,payload_sha256=excluded.payload_sha256", (snapshot_date.isoformat(), now, payload_sha256))
            self._db.execute("DELETE FROM derivative_contracts WHERE snapshot_date=?", (snapshot_date.isoformat(),))
            if rows:
                self._db.executemany("INSERT INTO derivative_contracts(snapshot_date,exchange,symbol,token,expiry,instrument_type,underlying,lot_size) VALUES(?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def upsert(self, records: Iterable[ContractRecord]) -> int:
        """Compatibility helper: store records under their supplied snapshot date, or today."""
        records = tuple(records)
        snapshot = next((r.snapshot_date for r in records if r.snapshot_date), None) or date.today()
        return self.upsert_snapshot(snapshot, records)

    def snapshot_dates(self) -> tuple[date, ...]:
        rows = self._db.execute("SELECT snapshot_date FROM contract_master_snapshots ORDER BY snapshot_date").fetchall()
        return tuple(date.fromisoformat(r[0]) for r in rows)

    def latest_snapshot_date(self) -> date | None:
        row = self._db.execute("SELECT snapshot_date FROM contract_master_snapshots ORDER BY snapshot_date DESC LIMIT 1").fetchone()
        return None if row is None else date.fromisoformat(row[0])

    def contracts(self, *, exchange: str, underlying: str, as_of: date, instrument_type: str = "STOCK_FUTURE") -> tuple[ContractRecord, ...]:
        row = self._db.execute("SELECT snapshot_date FROM contract_master_snapshots WHERE snapshot_date<=? ORDER BY snapshot_date DESC LIMIT 1", (as_of.isoformat(),)).fetchone()
        if row is None:
            raise LookupError(f"no historical contract-master snapshot for {as_of.isoformat()}")
        snapshot = row[0]
        rows = self._db.execute("""SELECT exchange,symbol,token,expiry,instrument_type,underlying,lot_size
            FROM derivative_contracts WHERE snapshot_date=? AND exchange=? AND underlying=?
            AND instrument_type=? AND expiry>=? ORDER BY expiry""", (snapshot, exchange, underlying, instrument_type, as_of.isoformat())).fetchall()
        return tuple(ContractRecord(r[0], r[1], r[2], date.fromisoformat(r[3]), r[4], r[5], int(r[6]), date.fromisoformat(snapshot)) for r in rows)

    def resolve(self, *, exchange: str, underlying: str, as_of: date, mode: str) -> ContractRecord:
        mode = mode.upper()
        if mode not in {"CURRENT", "NEAR"}:
            raise ValueError("mode must be CURRENT or NEAR")
        contracts = self.contracts(exchange=exchange, underlying=underlying, as_of=as_of)
        if not contracts:
            raise LookupError(f"no historical stock futures contract for {underlying} on {as_of.isoformat()}")
        return contracts[0] if mode == "CURRENT" else (contracts[1] if len(contracts) > 1 else contracts[0])
