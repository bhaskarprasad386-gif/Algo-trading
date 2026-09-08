"""Durable incremental storage for Cash-Future backtest results."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from .cash_future_replay import CashFutureBothReplayTrade, CashFutureReplayTrade


@dataclass(frozen=True)
class CashFutureResultSummary:
    trade_count: int
    gross_pnl: float
    net_pnl: float


class CashFutureResultStore:
    """Persist Cash-Future trades incrementally without retaining full history in RAM."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS cash_future_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                entry_timestamp_ns INTEGER NOT NULL,
                exit_timestamp_ns INTEGER NOT NULL,
                mode TEXT NOT NULL,
                current_instrument TEXT,
                near_instrument TEXT,
                future_instrument TEXT,
                quantity INTEGER NOT NULL,
                lot_size INTEGER NOT NULL,
                gross_pnl REAL NOT NULL,
                net_pnl REAL NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS ux_cash_future_results_identity
            ON cash_future_results (
                run_id, entry_timestamp_ns, exit_timestamp_ns, mode,
                COALESCE(future_instrument, ''), COALESCE(current_instrument, ''),
                COALESCE(near_instrument, '')
            )"""
        )
        self.connection.commit()

    @staticmethod
    def _row(run_id: str, trade: CashFutureReplayTrade | CashFutureBothReplayTrade) -> tuple:
        if isinstance(trade, CashFutureReplayTrade):
            return (run_id, trade.entry_timestamp_ns, trade.exit_timestamp_ns, "CURRENT",
                    None, None, trade.future_instrument, trade.quantity, trade.lot_size,
                    trade.result.gross_pnl, trade.result.net_pnl)
        return (run_id, trade.entry_timestamp_ns, trade.exit_timestamp_ns, "BOTH",
                trade.current_instrument, trade.near_instrument, None, trade.quantity,
                trade.lot_size, trade.gross_pnl, trade.net_pnl)

    def append(self, run_id: str, trade: CashFutureReplayTrade | CashFutureBothReplayTrade) -> bool:
        before = self.connection.total_changes
        self.connection.execute(
            """INSERT OR IGNORE INTO cash_future_results
            (run_id, entry_timestamp_ns, exit_timestamp_ns, mode, current_instrument,
             near_instrument, future_instrument, quantity, lot_size, gross_pnl, net_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", self._row(run_id, trade)
        )
        self.connection.commit()
        return self.connection.total_changes > before

    def append_many(self, run_id: str, trades: Iterable[CashFutureReplayTrade | CashFutureBothReplayTrade]) -> int:
        rows = [self._row(run_id, trade) for trade in trades]
        if not rows:
            return 0
        before = self.connection.total_changes
        self.connection.executemany(
            """INSERT OR IGNORE INTO cash_future_results
            (run_id, entry_timestamp_ns, exit_timestamp_ns, mode, current_instrument,
             near_instrument, future_instrument, quantity, lot_size, gross_pnl, net_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows
        )
        self.connection.commit()
        return self.connection.total_changes - before

    def summary(self, run_id: str) -> CashFutureResultSummary:
        row = self.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(gross_pnl), 0.0), COALESCE(SUM(net_pnl), 0.0) "
            "FROM cash_future_results WHERE run_id = ?", (run_id,)
        ).fetchone()
        return CashFutureResultSummary(int(row[0]), float(row[1]), float(row[2]))

    def count(self, run_id: str) -> int:
        return self.summary(run_id).trade_count
