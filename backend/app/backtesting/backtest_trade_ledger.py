"""Durable, idempotent SQLite ledger for completed backtest trades."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from app.backtesting.engine import BacktestTrade


class BacktestTradeLedger:
    """Persist completed trades without retaining the full ledger in memory.

    Each trade has a caller-supplied stable trade_id. Replaying the same chunk is
    therefore safe: identical rows are ignored and conflicting rows are rejected.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_trades (
                    job_id TEXT NOT NULL,
                    trade_id TEXT NOT NULL,
                    entry_timestamp TEXT NOT NULL,
                    exit_timestamp TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    gross_pnl REAL NOT NULL,
                    costs REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (job_id, trade_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_backtest_trades_job_entry "
                "ON backtest_trades(job_id, entry_timestamp)"
            )

    def append_chunk(
        self,
        job_id: str,
        trade_ids: Iterable[str],
        trades: Iterable[BacktestTrade],
        *,
        metadata: dict[str, object] | None = None,
    ) -> int:
        """Append a completed chunk transactionally and return inserted count."""
        if not job_id.strip():
            raise ValueError("job_id is required")
        ids = tuple(trade_ids)
        rows = tuple(trades)
        if len(ids) != len(rows):
            raise ValueError("trade_ids and trades must have the same length")
        if len(set(ids)) != len(ids) or any(not trade_id.strip() for trade_id in ids):
            raise ValueError("trade_ids must be non-empty and unique within a chunk")

        metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        inserted = 0
        with self._connect() as connection:
            for trade_id, trade in zip(ids, rows):
                values = (
                    job_id,
                    trade_id,
                    str(trade.entry_timestamp),
                    str(trade.exit_timestamp),
                    trade.entry_price,
                    trade.exit_price,
                    trade.quantity,
                    trade.gross_pnl,
                    trade.costs,
                    trade.net_pnl,
                    metadata_json,
                )
                existing = connection.execute(
                    "SELECT entry_timestamp, exit_timestamp, entry_price, exit_price, "
                    "quantity, gross_pnl, costs, net_pnl, metadata_json "
                    "FROM backtest_trades WHERE job_id=? AND trade_id=?",
                    (job_id, trade_id),
                ).fetchone()
                if existing is not None:
                    if existing != values[2:]:
                        raise ValueError(f"conflicting trade replay for {job_id}:{trade_id}")
                    continue
                connection.execute(
                    "INSERT INTO backtest_trades "
                    "(job_id, trade_id, entry_timestamp, exit_timestamp, entry_price, "
                    "exit_price, quantity, gross_pnl, costs, net_pnl, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                inserted += 1
        return inserted

    def count(self, job_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM backtest_trades WHERE job_id=?", (job_id,)
            ).fetchone()
        return int(row[0])

    def net_pnl(self, job_id: str) -> float:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(net_pnl), 0.0) FROM backtest_trades WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return float(row[0])

    def trades(self, job_id: str) -> tuple[BacktestTrade, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT entry_timestamp, exit_timestamp, entry_price, exit_price, quantity, "
                "gross_pnl, costs, net_pnl FROM backtest_trades "
                "WHERE job_id=? ORDER BY entry_timestamp, trade_id",
                (job_id,),
            ).fetchall()
        return tuple(BacktestTrade(*row) for row in rows)
