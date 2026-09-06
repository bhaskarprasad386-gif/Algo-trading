"""Historical derivative contract master and expiry-aware resolver.

The resolver is deliberately data-driven: it never manufactures a futures token
or substitutes a currently listed contract for a historical replay date.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
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

    def __post_init__(self) -> None:
        for value, name in ((self.exchange, "exchange"), (self.symbol, "symbol"), (self.token, "token"), (self.instrument_type, "instrument_type"), (self.underlying, "underlying")):
            if not str(value).strip():
                raise ValueError(f"{name} is required")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")


class ContractMasterCatalog:
    """Durable SQLite cache of contract-master snapshots."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS derivative_contracts (
                exchange TEXT NOT NULL, symbol TEXT NOT NULL, token TEXT NOT NULL,
                expiry TEXT NOT NULL, instrument_type TEXT NOT NULL, underlying TEXT NOT NULL,
                lot_size INTEGER NOT NULL,
                PRIMARY KEY(exchange, token)
            )"""
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_derivative_contracts_lookup ON derivative_contracts(exchange, underlying, expiry, instrument_type)")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def upsert(self, records: Iterable[ContractRecord]) -> int:
        rows = [
            (r.exchange, r.symbol, r.token, r.expiry.isoformat(), r.instrument_type, r.underlying, r.lot_size)
            for r in records
        ]
        if not rows:
            return 0
        self._db.executemany(
            """INSERT INTO derivative_contracts(exchange,symbol,token,expiry,instrument_type,underlying,lot_size)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(exchange,token) DO UPDATE SET symbol=excluded.symbol, expiry=excluded.expiry,
               instrument_type=excluded.instrument_type, underlying=excluded.underlying, lot_size=excluded.lot_size""",
            rows,
        )
        self._db.commit()
        return len(rows)

    def contracts(self, *, exchange: str, underlying: str, as_of: date, instrument_type: str = "STOCK_FUTURE") -> tuple[ContractRecord, ...]:
        rows = self._db.execute(
            """SELECT exchange,symbol,token,expiry,instrument_type,underlying,lot_size
               FROM derivative_contracts
               WHERE exchange=? AND underlying=? AND instrument_type=? AND expiry>=?
               ORDER BY expiry""",
            (exchange, underlying, instrument_type, as_of.isoformat()),
        ).fetchall()
        return tuple(ContractRecord(r[0], r[1], r[2], date.fromisoformat(r[3]), r[4], r[5], int(r[6])) for r in rows)

    def resolve(self, *, exchange: str, underlying: str, as_of: date, mode: str) -> ContractRecord:
        mode = mode.upper()
        if mode not in {"CURRENT", "NEAR"}:
            raise ValueError("mode must be CURRENT or NEAR")
        contracts = self.contracts(exchange=exchange, underlying=underlying, as_of=as_of)
        if not contracts:
            raise LookupError(f"no historical futures contract for {underlying} on {as_of.isoformat()}")
        # CURRENT and NEAR intentionally resolve from the same historical expiry set.
        # CURRENT is the nearest contract that was active on the replay date; NEAR is
        # the next listed expiry. This distinction is represented by the expiry set
        # retained in the master snapshot rather than by today's contract list.
        if mode == "CURRENT":
            return contracts[0]
        return contracts[1] if len(contracts) > 1 else contracts[0]
