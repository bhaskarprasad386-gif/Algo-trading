"""Durable SQLite ledger for incremental backtest trades and run summaries."""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from .engine import BacktestTrade


class BacktestTradeLedger:
    """Persist backtest trades and summary metrics durably."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY,
                initial_capital REAL NOT NULL,
                final_capital REAL NOT NULL,
                net_pnl REAL NOT NULL,
                total_return REAL NOT NULL,
                win_rate REAL NOT NULL,
                expectancy REAL NOT NULL,
                sharpe_ratio REAL,
                sortino_ratio REAL,
                max_drawdown REAL NOT NULL,
                cagr REAL
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS backtest_trades (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                entry_timestamp_json TEXT NOT NULL,
                exit_timestamp_json TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                gross_pnl REAL NOT NULL,
                costs REAL NOT NULL,
                net_pnl REAL NOT NULL,
                PRIMARY KEY(run_id, sequence)
            )
        """)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def save_run(self, run_id: str, result) -> None:
        if not run_id.strip():
            raise ValueError("run_id is required")
        self._db.execute(
            """INSERT INTO backtest_runs
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
              initial_capital=excluded.initial_capital,
              final_capital=excluded.final_capital,
              net_pnl=excluded.net_pnl,
              total_return=excluded.total_return,
              win_rate=excluded.win_rate,
              expectancy=excluded.expectancy,
              sharpe_ratio=excluded.sharpe_ratio,
              sortino_ratio=excluded.sortino_ratio,
              max_drawdown=excluded.max_drawdown,
              cagr=excluded.cagr""",
            (run_id, result.initial_capital, result.final_capital, result.net_pnl,
             result.total_return, result.win_rate, result.expectancy,
             result.sharpe_ratio, result.sortino_ratio, result.max_drawdown, result.cagr),
        )
        self._db.commit()

    def run_ids(self) -> tuple[str, ...]:
        """Return durable run identifiers in stable order."""
        rows = self._db.execute(
            "SELECT run_id FROM backtest_runs ORDER BY run_id"
        ).fetchall()
        return tuple(row[0] for row in rows)

    def run_summary(self, run_id: str) -> dict[str, object] | None:
        row = self._db.execute(
            "SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        keys = ("run_id", "initial_capital", "final_capital", "net_pnl", "total_return",
                "win_rate", "expectancy", "sharpe_ratio", "sortino_ratio", "max_drawdown", "cagr")
        return dict(zip(keys, row))

    def next_sequence(self, run_id: str) -> int:
        """Return the next unused trade sequence for a run."""
        if not run_id.strip():
            raise ValueError("run_id is required")
        row = self._db.execute(
            "SELECT COALESCE(MAX(sequence) + 1, 0) FROM backtest_trades WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row[0])

    def append(self, run_id: str, sequence: int, trades: Iterable[BacktestTrade]) -> int:
        if not run_id.strip() or sequence < 0:
            raise ValueError("run_id is required and sequence cannot be negative")
        rows = [
            (run_id, sequence + offset, json.dumps(trade.entry_timestamp, default=str),
             json.dumps(trade.exit_timestamp, default=str), trade.entry_price, trade.exit_price,
             trade.quantity, trade.gross_pnl, trade.costs, trade.net_pnl)
            for offset, trade in enumerate(trades)
        ]
        if not rows:
            return 0
        self._db.executemany(
            """INSERT INTO backtest_trades VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id, sequence) DO UPDATE SET
              entry_timestamp_json=excluded.entry_timestamp_json,
              exit_timestamp_json=excluded.exit_timestamp_json,
              entry_price=excluded.entry_price, exit_price=excluded.exit_price,
              quantity=excluded.quantity, gross_pnl=excluded.gross_pnl,
              costs=excluded.costs, net_pnl=excluded.net_pnl""", rows)
        self._db.commit()
        return len(rows)

    def append_next(self, run_id: str, trades: Iterable[BacktestTrade]) -> int:
        """Append trades at the next unused run-local sequence."""
        rows = tuple(trades)
        if not rows:
            return 0
        return self.append(run_id, self.next_sequence(run_id), rows)

    def count(self, run_id: str) -> int:
        return int(self._db.execute(
            "SELECT COUNT(*) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()[0])

    def net_pnl(self, run_id: str) -> float:
        return float(self._db.execute(
            "SELECT COALESCE(SUM(net_pnl),0) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()[0])

    def trades(self, run_id: str) -> tuple[BacktestTrade, ...]:
        rows = self._db.execute(
            """SELECT entry_timestamp_json,exit_timestamp_json,entry_price,exit_price,
                      quantity,gross_pnl,costs,net_pnl
               FROM backtest_trades WHERE run_id=? ORDER BY sequence""", (run_id,)).fetchall()
        return tuple(BacktestTrade(json.loads(r[0]), json.loads(r[1]), *r[2:]) for r in rows)

    def __enter__(self) -> "BacktestTradeLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
