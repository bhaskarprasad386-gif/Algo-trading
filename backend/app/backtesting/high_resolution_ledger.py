"""Durable incremental ledger adapter for high-resolution trades."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from app.backtesting.high_resolution_pnl import HighResolutionTrade
from app.backtesting.ledger import BacktestLedger, LedgerRecord


class HighResolutionLedgerWriter:
    """Persist completed high-resolution trades without retaining full history."""

    def __init__(self, ledger: BacktestLedger) -> None:
        self.ledger = ledger

    @staticmethod
    def _record(run_id: str, trade: HighResolutionTrade) -> LedgerRecord:
        return LedgerRecord(
            run_id=run_id,
            record_type="high_resolution_trade",
            timestamp_ns=trade.exit_timestamp_ns,
            payload=asdict(trade) | {"net_pnl": trade.net_pnl},
        )

    def append(self, run_id: str, trade: HighResolutionTrade) -> None:
        self.ledger.append(self._record(run_id, trade))

    def append_many(self, run_id: str, trades: Iterable[HighResolutionTrade]) -> int:
        return self.ledger.append_batch(self._record(run_id, trade) for trade in trades)
