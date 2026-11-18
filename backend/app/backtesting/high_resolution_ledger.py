"""Durable incremental persistence for completed high-resolution trades."""

from __future__ import annotations

from collections.abc import Iterable

from app.backtesting.high_resolution_pnl import HighResolutionTrade
from app.backtesting.ledger import BacktestLedger, LedgerRecord


HIGH_RESOLUTION_TRADE_RECORD = "high_resolution_trade"


class HighResolutionLedgerWriter:
    """Persist completed trades immediately so tick/event history stays out of RAM."""

    def __init__(self, ledger: BacktestLedger, run_id: str) -> None:
        if not run_id.strip():
            raise ValueError("run_id is required")
        self.ledger = ledger
        self.run_id = run_id

    @staticmethod
    def _record(run_id: str, trade: HighResolutionTrade) -> LedgerRecord:
        return LedgerRecord(
            run_id=run_id,
            record_type=HIGH_RESOLUTION_TRADE_RECORD,
            timestamp_ns=trade.exit_timestamp_ns,
            payload={
                "instrument": trade.instrument,
                "quantity": trade.quantity,
                "entry_timestamp_ns": trade.entry_timestamp_ns,
                "exit_timestamp_ns": trade.exit_timestamp_ns,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "gross_pnl": trade.gross_pnl,
                "fees": trade.fees,
                "net_pnl": trade.net_pnl,
            },
        )

    def append(self, trade: HighResolutionTrade) -> None:
        self.ledger.append(self._record(self.run_id, trade))

    def append_many(self, trades: Iterable[HighResolutionTrade]) -> int:
        return self.ledger.append_batch(self._record(self.run_id, trade) for trade in trades)

    def append_and_return(self, trade: HighResolutionTrade) -> HighResolutionTrade:
        self.append(trade)
        return trade

    def records(self):
        return self.ledger.records(self.run_id, HIGH_RESOLUTION_TRADE_RECORD)
