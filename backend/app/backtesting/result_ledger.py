"""Durable, run-isolated storage for incremental backtest results."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping


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
        if not self.trade_id.strip() or not self.instrument.strip() or not self.side.strip():
            raise ValueError("trade_id, instrument and side are required")
        if self.sequence < 0 or self.timestamp_ns < 0:
            raise ValueError("sequence/timestamp must be non-negative")
        numeric = (self.quantity, self.entry_price, self.gross_pnl, self.fees, self.slippage, self.net_pnl)
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("trade numeric values must be finite")
        if self.exit_price is not None and (not math.isfinite(float(self.exit_price)) or self.exit_price <= 0):
            raise ValueError("exit_price must be finite and positive")
        if self.quantity <= 0 or self.entry_price <= 0:
            raise ValueError("quantity and entry_price must be positive")
        if self.fees < 0 or self.slippage < 0:
            raise ValueError("fees and slippage must be non-negative")
        if self.strike is not None and (not math.isfinite(float(self.strike)) or self.strike <= 0):
            raise ValueError("strike must be finite and positive")


@dataclass(frozen=True)
class BacktestFill:
    """One immutable executable fill retained independently per run."""

    fill_id: str
    order_id: str
    sequence: int
    timestamp_ns: int
    instrument: str
    side: str
    quantity: float
    price: float
    fee: float = 0.0
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.fill_id.strip() or not self.order_id.strip() or not self.instrument.strip() or not self.side.strip():
            raise ValueError("fill_id, order_id, instrument and side are required")
        if self.sequence < 0 or self.timestamp_ns < 0:
            raise ValueError("sequence/timestamp must be non-negative")
        numeric = (self.quantity, self.price, self.fee)
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("fill numeric values must be finite")
        if self.quantity <= 0 or self.price <= 0:
            raise ValueError("quantity and price must be positive")
        if self.fee < 0:
            raise ValueError("fee must be non-negative")


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
        try:
            encoded = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError("event payload must be JSON-safe") from exc
        if not encoded:
            raise ValueError("event payload is required")


@dataclass(frozen=True)
class EquityPoint:
    timestamp_ns: int
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    drawdown: float

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("equity timestamp must be non-negative")
        values = (self.equity, self.realized_pnl, self.unrealized_pnl, self.drawdown)
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("equity values must be finite")
        if self.equity < 0 or self.drawdown < 0:
            raise ValueError("equity and drawdown cannot be negative")


class BacktestResultLedger:
    """SQLite WAL ledger; appends are transactional and idempotent."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._transaction_depth = 0
        self._create_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the ledger connection for same-database transaction composition."""
        return self._db

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Atomically compose multiple ledger/checkpoint writes on this connection."""
        if self._transaction_depth:
            yield
            return
        self._db.execute("BEGIN IMMEDIATE")
        self._transaction_depth = 1
        try:
            yield
        except Exception:
            self._db.rollback()
            raise
        else:
            self._db.commit()
        finally:
            self._transaction_depth = 0

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
            CREATE TABLE IF NOT EXISTS backtest_fills (
                run_id TEXT NOT NULL,
                fill_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                instrument TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                fee REAL NOT NULL,
                metadata_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                PRIMARY KEY (run_id, fill_id),
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
                equity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                equity REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                drawdown REAL NOT NULL,
                FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_backtest_trades_time ON backtest_trades(run_id, timestamp_ns, sequence);
            CREATE INDEX IF NOT EXISTS idx_backtest_fills_time ON backtest_fills(run_id, timestamp_ns, sequence);
            CREATE INDEX IF NOT EXISTS idx_backtest_events_time ON backtest_events(run_id, timestamp_ns, sequence);
            CREATE INDEX IF NOT EXISTS idx_backtest_equity_time ON backtest_equity(run_id, timestamp_ns, equity_id);
            """
        )
        self._migrate_equity_schema()
        self._db.commit()

    def _migrate_equity_schema(self) -> None:
        columns = [row[1] for row in self._db.execute("PRAGMA table_info(backtest_equity)")]
        if not columns or "equity_id" in columns:
            return
        self._db.executescript("""
            CREATE TABLE backtest_equity_new (
                equity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                equity REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                drawdown REAL NOT NULL,
                FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
            );
            INSERT INTO backtest_equity_new
                (run_id, timestamp_ns, equity, realized_pnl, unrealized_pnl, drawdown)
            SELECT run_id, timestamp_ns, equity, realized_pnl, unrealized_pnl, drawdown
            FROM backtest_equity ORDER BY rowid;
            DROP TABLE backtest_equity;
            ALTER TABLE backtest_equity_new RENAME TO backtest_equity;
            CREATE INDEX idx_backtest_equity_time
                ON backtest_equity(run_id, timestamp_ns, equity_id);
            """)
        self._db.commit()

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError("value must be JSON-safe") from exc

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def create_run(self, run_id: str, provenance: Mapping[str, Any], *, created_at_ns: int = 0) -> None:
        if not run_id.strip() or created_at_ns < 0:
            raise ValueError("run_id is required and created_at_ns must be non-negative")
        payload = self._json(provenance)
        try:
            self._db.execute(
                "INSERT INTO backtest_runs(run_id, provenance_json, created_at_ns) VALUES (?, ?, ?)",
                (run_id, payload, created_at_ns),
            )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"run already exists: {run_id}") from exc


    def run_provenance(self, run_id: str) -> dict[str, Any]:
        """Return persisted provenance for an existing run."""
        row = self.run(run_id)
        try:
            value = json.loads(row["provenance_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("stored run provenance is invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("stored run provenance must be an object")
        return value

    def claim_run(self, run_id: str) -> bool:
        """Atomically claim a CREATED run for one Universal worker."""
        self._require_run(run_id)
        with self.transaction():
            cursor = self._db.execute(
                "UPDATE backtest_runs SET status=? WHERE run_id=? AND status=?",
                ("RUNNING", run_id, "CREATED"),
            )
            return cursor.rowcount == 1

    def mark_recoverable(self, run_id: str) -> None:
        """Explicitly mark a run recoverable; caller must establish worker loss."""
        self._require_run(run_id)
        with self.transaction():
            cursor = self._db.execute(
                "UPDATE backtest_runs SET status=? WHERE run_id=? AND status=?",
                ("RECOVERABLE", run_id, "RUNNING"),
            )
            if cursor.rowcount != 1:
                raise ValueError("run is not RUNNING and cannot be marked recoverable")

    def claim_recoverable(self, run_id: str) -> bool:
        """Atomically claim an explicitly recoverable run for a new worker."""
        self._require_run(run_id)
        with self.transaction():
            cursor = self._db.execute(
                "UPDATE backtest_runs SET status=? WHERE run_id=? AND status=?",
                ("RUNNING", run_id, "RECOVERABLE"),
            )
            return cursor.rowcount == 1

    def set_status(self, run_id: str, status: str) -> None:
        self._require_run(run_id)
        status = status.strip().upper()
        if status not in {"CREATED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "RECOVERABLE"}:
            raise ValueError(f"invalid run status: {status}")
        current = self._db.execute(
            "SELECT status FROM backtest_runs WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        if current == "COMPLETED" and status != "COMPLETED":
            raise ValueError("completed run is immutable")
        self._db.execute("UPDATE backtest_runs SET status=? WHERE run_id=?", (status, run_id))
        self._db.commit()

    def _require_mutable_run(self, run_id: str) -> None:
        self._require_run(run_id)
        status = self._db.execute(
            "SELECT status FROM backtest_runs WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        if status == "COMPLETED":
            raise ValueError("completed run is immutable")

    def append_trades(self, run_id: str, trades: Iterable[BacktestTrade]) -> int:
        self._require_mutable_run(run_id)
        rows = []
        for trade in trades:
            metadata_json = self._json(dict(trade.metadata or {}))
            identity = self._json({
                "trade_id": trade.trade_id, "sequence": trade.sequence, "timestamp_ns": trade.timestamp_ns,
                "instrument": trade.instrument, "side": trade.side, "quantity": trade.quantity,
                "entry_price": trade.entry_price, "exit_price": trade.exit_price, "gross_pnl": trade.gross_pnl,
                "fees": trade.fees, "slippage": trade.slippage, "net_pnl": trade.net_pnl,
                "contract": trade.contract, "expiry": trade.expiry, "strike": trade.strike,
                "leg": trade.leg, "data_resolution": trade.data_resolution, "metadata": dict(trade.metadata or {}),
            })
            rows.append((run_id, trade.trade_id, trade.sequence, trade.timestamp_ns, trade.instrument, trade.side,
                         trade.quantity, trade.entry_price, trade.exit_price, trade.gross_pnl, trade.fees,
                         trade.slippage, trade.net_pnl, trade.contract, trade.expiry, trade.strike, trade.leg,
                         trade.data_resolution, metadata_json, self._hash(identity)))
        return self._insert_idempotent(
            """INSERT INTO backtest_trades
            (run_id,trade_id,sequence,timestamp_ns,instrument,side,quantity,entry_price,exit_price,gross_pnl,fees,slippage,net_pnl,contract,expiry,strike,leg,data_resolution,metadata_json,payload_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows, "backtest_trades")

    def append_fills(self, run_id: str, fills: Iterable[BacktestFill]) -> int:
        self._require_mutable_run(run_id)
        rows = []
        for fill in fills:
            metadata_json = self._json(dict(fill.metadata or {}))