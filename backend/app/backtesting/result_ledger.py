"""Durable, run-isolated storage for incremental backtest results."""

from __future__ import annotations

import hashlib
import json
import math
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

    def set_status(self, run_id: str, status: str) -> None:
        self._require_run(run_id)
        status = status.strip().upper()
        if status not in {"CREATED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError(f"invalid run status: {status}")
        self._db.execute("UPDATE backtest_runs SET status=? WHERE run_id=?", (status, run_id))
        self._db.commit()

    def append_trades(self, run_id: str, trades: Iterable[BacktestTrade]) -> int:
        self._require_run(run_id)
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
        self._require_run(run_id)
        rows = []
        for fill in fills:
            metadata_json = self._json(dict(fill.metadata or {}))
            identity = self._json({
                "fill_id": fill.fill_id, "order_id": fill.order_id, "sequence": fill.sequence,
                "timestamp_ns": fill.timestamp_ns, "instrument": fill.instrument, "side": fill.side,
                "quantity": fill.quantity, "price": fill.price, "fee": fill.fee,
                "metadata": dict(fill.metadata or {}),
            })
            rows.append((run_id, fill.fill_id, fill.order_id, fill.sequence, fill.timestamp_ns,
                         fill.instrument, fill.side, fill.quantity, fill.price, fill.fee,
                         metadata_json, self._hash(identity)))
        return self._insert_idempotent(
            """INSERT INTO backtest_fills
            (run_id,fill_id,order_id,sequence,timestamp_ns,instrument,side,quantity,price,fee,metadata_json,payload_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows, "backtest_fills")

    def append_events(self, run_id: str, events: Iterable[BacktestEvent]) -> int:
        self._require_run(run_id)
        rows = []
        for event in events:
            payload_json = self._json(event.payload)
            rows.append((run_id, event.sequence, event.timestamp_ns, event.event_type,
                         payload_json, self._hash(payload_json)))
        return self._insert_idempotent(
            "INSERT INTO backtest_events(run_id,sequence,timestamp_ns,event_type,payload_json,payload_hash) VALUES (?,?,?,?,?,?)",
            rows, "backtest_events")

    def append_equity(self, run_id: str, points: Iterable[EquityPoint]) -> int:
        self._require_run(run_id)
        rows = [(run_id, p.timestamp_ns, p.equity, p.realized_pnl, p.unrealized_pnl, p.drawdown) for p in points]
        return self._insert_idempotent(
            "INSERT INTO backtest_equity(run_id,timestamp_ns,equity,realized_pnl,unrealized_pnl,drawdown) VALUES (?,?,?,?,?,?)",
            rows, "backtest_equity")

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
                                "SELECT payload_hash FROM backtest_trades WHERE run_id=? AND trade_id=?", (row[0], row[1])
                            ).fetchone()
                            if existing and existing[0] == row[-1]:
                                continue
                        elif table == "backtest_fills":
                            existing = self._db.execute(
                                "SELECT payload_hash FROM backtest_fills WHERE run_id=? AND fill_id=?", (row[0], row[1])
                            ).fetchone()
                            if existing and existing[0] == row[-1]:
                                continue
                            sequence_existing = self._db.execute(
                                "SELECT payload_hash FROM backtest_fills WHERE run_id=? AND sequence=?", (row[0], row[3])
                            ).fetchone()
                            if sequence_existing is not None:
                                raise ValueError("conflicting duplicate fill sequence")
                        elif table == "backtest_events":
                            existing = self._db.execute(
                                "SELECT payload_hash FROM backtest_events WHERE run_id=? AND sequence=?", (row[0], row[1])
                            ).fetchone()
                            if existing and existing[0] == row[-1]:
                                continue
                        elif table == "backtest_equity":
                            existing = self._db.execute(
                                "SELECT 1 FROM backtest_equity WHERE run_id=? AND timestamp_ns=? AND equity=? AND realized_pnl=? AND unrealized_pnl=? AND drawdown=?",
                                (row[0], row[1], row[2], row[3], row[4], row[5]),
                            ).fetchone()
                            if existing:
                                continue
                        raise ValueError(f"conflicting duplicate in {table}")
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"invalid {table} append") from exc
        return inserted

    def _require_run(self, run_id: str) -> None:
        if self._db.execute("SELECT 1 FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone() is None:
            raise ValueError(f"unknown run: {run_id}")

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")

    def trades(self, run_id: str, *, limit: int = 500, after_sequence: int = -1) -> list[sqlite3.Row]:
        self._require_run(run_id)
        self._validate_limit(limit)
        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")
        return list(self._db.execute(
            "SELECT * FROM backtest_trades WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (run_id, after_sequence, limit)))

    def fills(self, run_id: str, *, limit: int = 500, after_sequence: int = -1) -> list[sqlite3.Row]:
        self._require_run(run_id)
        self._validate_limit(limit)
        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")
        return list(self._db.execute(
            "SELECT * FROM backtest_fills WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (run_id, after_sequence, limit)))

    def events(self, run_id: str, *, limit: int = 500, after_sequence: int = -1) -> list[sqlite3.Row]:
        self._require_run(run_id)
        self._validate_limit(limit)
        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")
        return list(self._db.execute(
            "SELECT * FROM backtest_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (run_id, after_sequence, limit)))

    def equity(
        self, run_id: str, *, limit: int = 500, after_timestamp_ns: int = -1,
        after_equity_id: int = -1,
    ) -> list[sqlite3.Row]:
        self._require_run(run_id)
        self._validate_limit(limit)
        if after_timestamp_ns < -1 or after_equity_id < -1:
            raise ValueError("equity cursor values must be >= -1")
        return list(self._db.execute(
            "SELECT * FROM backtest_equity WHERE run_id=? AND (timestamp_ns>? OR (timestamp_ns=? AND equity_id>?)) ORDER BY timestamp_ns, equity_id LIMIT ?",
            (run_id, after_timestamp_ns, after_timestamp_ns, after_equity_id, limit)))

    def run(self, run_id: str) -> sqlite3.Row:
        self._require_run(run_id)
        return self._db.execute("SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
