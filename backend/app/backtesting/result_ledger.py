"""Durable, run-isolated storage for incremental backtest results."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class BacktestTrade:
    """One immutable trade/fill result with exact execution provenance."""

    trade_id: str
    sequence: int
    timestamp_ns: int
    instrument: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float | None
    gross_pnl: float
    fees: float
    slippage: float
    net_pnl: float
    contract: str = ""
    expiry: str = ""
    strike: float | None = None
    leg: str = ""
    data_resolution: str = ""
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            raise ValueError("trade_id is required")
        if self.sequence < 0 or self.timestamp_ns < 0:
            raise ValueError("sequence/timestamp must be non-negative")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True)
class BacktestEvent:
    """Incremental event/audit record retained independently per run."""

    sequence: int
    timestamp_ns: int
    event_type: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.timestamp_ns < 0:
            raise ValueError("sequence/timestamp must be non-negative")
        if not self.event_type.strip():
            raise ValueError("event_type is required")


@dataclass(frozen=True)
class EquityPoint:
    timestamp_ns: int
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    drawdown: float


class BacktestResultLedger:
    """SQLite WAL ledger; appends are transactional and idempotent.

    Results are partitioned by run_id, so concurrent/independent backtests can
    never mix trades, events or equity points. Data is written incrementally;
    callers do not need to keep a complete historical result set in RAM.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def close(self) -> None:
        self._db.close()

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY,
                provenance_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'CREATED',
                created_at_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS backtest_trades (
                run_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                instrument TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                gross_pnl REAL NOT NULL,
                fees REAL NOT NULL,
                slippage REAL NOT NULL,
                net_pnl REAL NOT NULL,
                contract TEXT NOT NULL,
                expiry TEXT NOT NULL,
                strike REAL,
                leg TEXT NOT NULL,
                data_resolution TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                PRIMARY KEY (run_id, trade_id),
                UNIQUE (run_id, sequence),
                FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS backtest_events (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence),
                FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS backtest_equity (
                run_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                equity REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                drawdown REAL NOT NULL,
                PRIMARY KEY (run_id, timestamp_ns),
                FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_backtest_trades_time
                ON backtest_trades(run_id, timestamp_ns, sequence);
            CREATE INDEX IF NOT EXISTS idx_backtest_events_time
                ON backtest_events(run_id, timestamp_ns, sequence);
            CREATE INDEX IF NOT EXISTS idx_backtest_equity_time
                ON backtest_equity(run_id, timestamp_ns);
            """
        )
        self._db.commit()

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def create_run(self, run_id: str, provenance: Mapping[str, Any], *, created_at_ns: int = 0) -> None:
        if not run_id.strip():
            raise ValueError("run_id is required")
        payload = self._json(provenance)
        try:
            self._db.execute(
                "INSERT INTO backtest_runs(run_id, provenance_json, created_at_ns) VALUES (?, ?, ?)",
                (run_id, payload, created_at_ns),
            )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"run already exists: {run_id}") from exc

    def set_status(self, run_id: str, status: str) -> None:
        self._require_run(run_id)
        self._db.execute("UPDATE backtest_runs SET status=? WHERE run_id=?", (status, run_id))
        self._db.commit()

    def append_trades(self, run_id: str, trades: Iterable[BacktestTrade]) -> int:
        self._require_run(run_id)
        rows = []
        for trade in trades:
            metadata_json = self._json(dict(trade.metadata or {}))
            identity = self._json({
                "trade_id": trade.trade_id, "sequence": trade.sequence,
                "timestamp_ns": trade.timestamp_ns, "instrument": trade.instrument,
                "side": trade.side, "quantity": trade.quantity,
                "entry_price": trade.entry_price, "exit_price": trade.exit_price,
                "gross_pnl": trade.gross_pnl, "fees": trade.fees, "slippage": trade.slippage,
                "net_pnl": trade.net_pnl, "contract": trade.contract, "expiry": trade.expiry,
                "strike": trade.strike, "leg": trade.leg, "data_resolution": trade.data_resolution,
                "metadata": dict(trade.metadata or {}),
            })
            rows.append((
                run_id, trade.trade_id, trade.sequence, trade.timestamp_ns, trade.instrument,
                trade.side, trade.quantity, trade.entry_price, trade.exit_price, trade.gross_pnl,
                trade.fees, trade.slippage, trade.net_pnl, trade.contract, trade.expiry,
                trade.strike, trade.leg, trade.data_resolution, metadata_json, self._hash(identity),
            ))
        return self._insert_idempotent(
            """INSERT INTO backtest_trades
            (run_id,trade_id,sequence,timestamp_ns,instrument,side,quantity,entry_price,exit_price,
             gross_pnl,fees,slippage,net_pnl,contract,expiry,strike,leg,data_resolution,metadata_json,payload_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
            "backtest_trades",
        )

    def append_events(self, run_id: str, events: Iterable[BacktestEvent]) -> int:
        self._require_run(run_id)
        rows = []
        for event in events:
            payload_json = self._json(event.payload)
            rows.append((run_id, event.sequence, event.timestamp_ns, event.event_type,
                         payload_json, self._hash(payload_json)))
        return self._insert_idempotent(
            "INSERT INTO backtest_events(run_id,sequence,timestamp_ns,event_type,payload_json,payload_hash) VALUES (?,?,?,?,?,?)",
            rows,
            "backtest_events",
        )

    def append_equity(self, run_id: str, points: Iterable[EquityPoint]) -> int:
        self._require_run(run_id)
        rows = [(run_id, p.timestamp_ns, p.equity, p.realized_pnl, p.unrealized_pnl, p.drawdown) for p in points]
        return self._insert_idempotent(
            "INSERT INTO backtest_equity(run_id,timestamp_ns,equity,realized_pnl,unrealized_pnl,drawdown) VALUES (?,?,?,?,?,?)",
            rows,
            "backtest_equity",
        )

    def _insert_idempotent(self, sql: str, rows: list[tuple[Any, ...]], table: str) -> int:
        if not rows:
            return 0
        inserted = 0
        try:
            with self._db:
                for row in rows:
                    try:
                        self._db.execute(sql, row)
                        inserted += 1
                    except sqlite3.IntegrityError:
                        if table == "backtest_trades":
                            existing = self._db.execute(
                                "SELECT payload_hash FROM backtest_trades WHERE run_id=? AND trade_id=?",
                                (row[0], row[1]),
                            ).fetchone()
                            if existing and existing[0] == row[-1]:
                                continue
                        elif table == "backtest_events":
                            existing = self._db.execute(
                                "SELECT payload_hash FROM backtest_events WHERE run_id=? AND sequence=?",
                                (row[0], row[1]),
                            ).fetchone()
                            if existing and existing[0] == row[-1]:
                                continue
                        elif table == "backtest_equity":
                            existing = self._db.execute(
                                "SELECT equity,realized_pnl,unrealized_pnl,drawdown FROM backtest_equity WHERE run_id=? AND timestamp_ns=?",
                                (row[0], row[1]),
                            ).fetchone()
                            if existing and tuple(existing) == tuple(row[2:]):
                                continue
                        raise ValueError(f"conflicting duplicate in {table}")
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"invalid {table} append") from exc
        return inserted

    def _require_run(self, run_id: str) -> None:
        if self._db.execute("SELECT 1 FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone() is None:
            raise ValueError(f"unknown run: {run_id}")

    def trades(self, run_id: str, *, limit: int = 500, after_sequence: int = -1) -> list[sqlite3.Row]:
        self._require_run(run_id)
        return list(self._db.execute(
            "SELECT * FROM backtest_trades WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (run_id, after_sequence, limit),
        ))

    def events(self, run_id: str, *, limit: int = 500, after_sequence: int = -1) -> list[sqlite3.Row]:
        self._require_run(run_id)
        return list(self._db.execute(
            "SELECT * FROM backtest_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (run_id, after_sequence, limit),
        ))

    def equity(self, run_id: str, *, limit: int = 500, after_timestamp_ns: int = -1) -> list[sqlite3.Row]:
        self._require_run(run_id)
        return list(self._db.execute(
            "SELECT * FROM backtest_equity WHERE run_id=? AND timestamp_ns>? ORDER BY timestamp_ns LIMIT ?",
            (run_id, after_timestamp_ns, limit),
        ))

    def run(self, run_id: str) -> sqlite3.Row:
        self._require_run(run_id)
        return self._db.execute("SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
