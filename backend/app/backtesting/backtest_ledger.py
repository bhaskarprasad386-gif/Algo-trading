"""Durable SQLite ledger for incremental backtest trades and run summaries."""

from __future__ import annotations

import json
import math
import sqlite3
from threading import RLock
from typing import Iterable

from .engine import BacktestTrade


class BacktestTradeLedger:
    """Persist backtest trades, checkpoints, and summary metrics durably."""

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id TEXT PRIMARY KEY, initial_capital REAL NOT NULL, final_capital REAL NOT NULL,
            net_pnl REAL NOT NULL, total_return REAL NOT NULL, win_rate REAL NOT NULL,
            expectancy REAL NOT NULL, sharpe_ratio REAL, sortino_ratio REAL,
            max_drawdown REAL NOT NULL, cagr REAL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS backtest_trades (
            run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            entry_timestamp_json TEXT NOT NULL, exit_timestamp_json TEXT NOT NULL,
            entry_price REAL NOT NULL, exit_price REAL NOT NULL, quantity REAL NOT NULL,
            gross_pnl REAL NOT NULL, costs REAL NOT NULL, net_pnl REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id, sequence))""")
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(backtest_trades)")}
        if "metadata_json" not in columns:
            self._db.execute("ALTER TABLE backtest_trades ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        self._db.execute("""CREATE TABLE IF NOT EXISTS backtest_checkpoints (
            run_id TEXT PRIMARY KEY, cursor TEXT NOT NULL, trade_count INTEGER NOT NULL)""")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _finite(value: object, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
        return float(value)

    def save_run(self, run_id: str, result) -> None:
        with self._lock:
            if not isinstance(run_id, str) or not run_id.strip():
                raise ValueError("run_id is required")
            values = (self._finite(result.initial_capital, "initial_capital"), self._finite(result.final_capital, "final_capital"), self._finite(result.net_pnl, "net_pnl"), self._finite(result.total_return, "total_return"), self._finite(result.win_rate, "win_rate"), self._finite(result.expectancy, "expectancy"), result.sharpe_ratio, result.sortino_ratio, self._finite(result.max_drawdown, "max_drawdown"), result.cagr)
            for name, value in (("sharpe_ratio", values[6]), ("sortino_ratio", values[7]), ("cagr", values[9])):
                if value is not None:
                    self._finite(value, name)
            self._db.execute("""INSERT INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET initial_capital=excluded.initial_capital, final_capital=excluded.final_capital,
                net_pnl=excluded.net_pnl, total_return=excluded.total_return, win_rate=excluded.win_rate,
                expectancy=excluded.expectancy, sharpe_ratio=excluded.sharpe_ratio, sortino_ratio=excluded.sortino_ratio,
                max_drawdown=excluded.max_drawdown, cagr=excluded.cagr""", (run_id, *values))
            self._db.commit()

    def run_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(row[0] for row in self._db.execute("SELECT run_id FROM backtest_runs ORDER BY run_id"))

    def run_summary(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            keys = ("run_id", "initial_capital", "final_capital", "net_pnl", "total_return", "win_rate", "expectancy", "sharpe_ratio", "sortino_ratio", "max_drawdown", "cagr")
            return dict(zip(keys, row))

    def save_checkpoint(self, run_id: str, cursor: str, trade_count: int) -> None:
        with self._lock:
            if not isinstance(run_id, str) or not run_id.strip() or not isinstance(cursor, str) or not cursor.strip():
                raise ValueError("run_id and cursor are required")
            if type(trade_count) is not int or trade_count < 0:
                raise ValueError("trade_count must be a non-negative integer")
            current = self._db.execute("SELECT cursor,trade_count FROM backtest_checkpoints WHERE run_id=?", (run_id,)).fetchone()
            if current is not None and trade_count < int(current[1]):
                raise ValueError("checkpoint trade_count cannot move backwards")
            self._db.execute("""INSERT INTO backtest_checkpoints(run_id,cursor,trade_count) VALUES(?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET cursor=excluded.cursor, trade_count=excluded.trade_count""", (run_id, cursor, trade_count))
            self._db.commit()

    def checkpoint(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            if not isinstance(run_id, str) or not run_id.strip():
                raise ValueError("run_id is required")
            row = self._db.execute("SELECT cursor,trade_count FROM backtest_checkpoints WHERE run_id=?", (run_id,)).fetchone()
            return None if row is None else {"cursor": row[0], "trade_count": int(row[1])}

    def clear_checkpoint(self, run_id: str) -> None:
        with self._lock:
            if not isinstance(run_id, str) or not run_id.strip():
                raise ValueError("run_id is required")
            self._db.execute("DELETE FROM backtest_checkpoints WHERE run_id=?", (run_id,))
            self._db.commit()

    def next_sequence(self, run_id: str) -> int:
        with self._lock:
            if not isinstance(run_id, str) or not run_id.strip():
                raise ValueError("run_id is required")
            row = self._db.execute("SELECT COALESCE(MAX(sequence) + 1, 0) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()
            return int(row[0])

    @staticmethod
    def _metadata(trade: BacktestTrade) -> str:
        return json.dumps({"entry_market_price": trade.entry_market_price, "exit_market_price": trade.exit_market_price, "entry_price_source": trade.entry_price_source, "exit_price_source": trade.exit_price_source, "entry_event_identity": trade.entry_event_identity, "exit_event_identity": trade.exit_event_identity}, sort_keys=True, default=str)

    @staticmethod
    def _row_payload(row) -> tuple:
        return tuple(row)

    def append(self, run_id: str, sequence: int, trades: Iterable[BacktestTrade]) -> int:
        with self._lock:
            if not isinstance(run_id, str) or not run_id.strip() or type(sequence) is not int or sequence < 0:
                raise ValueError("run_id is required and sequence must be a non-negative integer")
            rows = []
            for offset, trade in enumerate(trades):
                if not isinstance(trade, BacktestTrade):
                    raise TypeError("trades must contain BacktestTrade values")
                rows.append((run_id, sequence + offset, json.dumps(trade.entry_timestamp, default=str), json.dumps(trade.exit_timestamp, default=str), self._finite(trade.entry_price, "entry_price"), self._finite(trade.exit_price, "exit_price"), self._finite(trade.quantity, "quantity"), self._finite(trade.gross_pnl, "gross_pnl"), self._finite(trade.costs, "costs"), self._finite(trade.net_pnl, "net_pnl"), self._metadata(trade)))
            if not rows:
                return 0
            existing = self._db.execute("SELECT sequence,entry_timestamp_json,exit_timestamp_json,entry_price,exit_price,quantity,gross_pnl,costs,net_pnl,metadata_json FROM backtest_trades WHERE run_id=? AND sequence BETWEEN ? AND ? ORDER BY sequence", (run_id, sequence, sequence + len(rows) - 1)).fetchall()
            if existing:
                expected = [tuple(row[1:]) for row in rows]
                actual = [tuple(row[1:]) for row in existing]
                if len(existing) != len(rows) or actual != expected:
                    raise ValueError(f"trade sequence already exists: {existing[0][0]}")
                return len(rows)
            try:
                self._db.executemany("""INSERT INTO backtest_trades
                    (run_id,sequence,entry_timestamp_json,exit_timestamp_json,entry_price,exit_price,quantity,gross_pnl,costs,net_pnl,metadata_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", rows)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
            return len(rows)

    def append_next(self, run_id: str, trades: Iterable[BacktestTrade]) -> int:
        rows = tuple(trades)
        if not rows:
            return 0
        with self._lock:
            return self.append(run_id, self.next_sequence(run_id), rows)

    def count(self, run_id: str) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()[0])

    def net_pnl(self, run_id: str) -> float:
        with self._lock:
            return float(self._db.execute("SELECT COALESCE(SUM(net_pnl),0) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()[0])

    def trades(self, run_id: str) -> tuple[BacktestTrade, ...]:
        with self._lock:
            rows = self._db.execute("""SELECT entry_timestamp_json,exit_timestamp_json,entry_price,exit_price,quantity,gross_pnl,costs,net_pnl,metadata_json FROM backtest_trades WHERE run_id=? ORDER BY sequence""", (run_id,)).fetchall()
            result = []
            for row in rows:
                metadata = json.loads(row[8])
                result.append(BacktestTrade(json.loads(row[0]), json.loads(row[1]), *row[2:8], metadata.get("entry_market_price"), metadata.get("exit_market_price"), metadata.get("entry_price_source"), metadata.get("exit_price_source"), tuple(metadata["entry_event_identity"]) if metadata.get("entry_event_identity") is not None else None, tuple(metadata["exit_event_identity"]) if metadata.get("exit_event_identity") is not None else None))
            return tuple(result)

    def __enter__(self) -> "BacktestTradeLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
