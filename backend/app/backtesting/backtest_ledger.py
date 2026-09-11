"""Durable SQLite ledger for incremental backtest trades."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Iterable

from .engine import BacktestTrade


class BacktestTradeLedger:
    """Persist trades in bounded batches so long backtests do not retain them in RAM."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS backtest_trades (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                entry_timestamp TEXT NOT NULL,
                exit_timestamp TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                gross_pnl REAL NOT NULL,
                costs REAL NOT NULL,
                net_pnl REAL NOT NULL,
                PRIMARY KEY(run_id, sequence)
            )"""
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def append(self, run_id: str, sequence: int, trades: Iterable[BacktestTrade]) -> int:
        if not run_id.strip() or sequence < 0:
            raise ValueError("run_id is required and sequence cannot be negative")
        rows = [
            (
                run_id,
                sequence + offset,
                str(trade.entry_timestamp),
                str(trade.exit_timestamp),
                trade.entry_price,
                trade.exit_price,
                trade.quantity,
                trade.gross_pnl,
                trade.costs,
                trade.net_pnl,
            )
            for offset, trade in enumerate(trades)
        ]
        if not rows:
            return 0
        self._db.executemany(
            "INSERT INTO backtest_trades VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, sequence) DO UPDATE SET "
            "entry_timestamp=excluded.entry_timestamp, exit_timestamp=excluded.exit_timestamp, "
            "entry_price=excluded.entry_price, exit_price=excluded.exit_price, quantity=excluded.quantity, "
            "gross_pnl=excluded.gross_pnl, costs=excluded.costs, net_pnl=excluded.net_pnl",
            rows,
        )
        self._db.commit()
        return len(rows)

    def count(self, run_id: str) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()[0])

    def net_pnl(self, run_id: str) -> float:
        return float(self._db.execute("SELECT COALESCE(SUM(net_pnl),0) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()[0])

    def trades(self, run_id: str) -> tuple[BacktestTrade, ...]:
        rows = self._db.execute(
            "SELECT entry_timestamp,exit_timestamp,entry_price,exit_price,quantity,gross_pnl,costs,net_pnl "
            "FROM backtest_trades WHERE run_id=? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        return tuple(BacktestTrade(*row) for row in rows)

    def __enter__(self) -> "BacktestTradeLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
