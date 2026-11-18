"""Durable cash/future backtest result persistence on the generic ledger."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from .cash_future_replay import CashFutureReplayTrade
from .ledger import BacktestLedger, LedgerRecord


class CashFutureLedgerWriter:
    """Persist each completed cash/future trade incrementally."""

    def __init__(self, ledger: BacktestLedger, run_id: str) -> None:
        if not run_id.strip():
            raise ValueError("run_id is required")
        self.ledger = ledger
        self.run_id = run_id

    @staticmethod
    def _payload(trade: CashFutureReplayTrade) -> dict:
        result = asdict(trade.result)
        return {
            "entry_timestamp_ns": trade.entry_timestamp_ns,
            "exit_timestamp_ns": trade.exit_timestamp_ns,
            "future_instrument": trade.future_instrument,
            "lot_size": trade.lot_size,
            "quantity": trade.quantity,
            "spot_entry": result["spot_entry"],
            "spot_exit": result["spot_exit"],
            "future_entry": result["future_entry"],
            "future_exit": result["future_exit"],
            "units": trade.result.units,
            "spot_pnl": trade.result.spot_pnl,
            "future_pnl": trade.result.future_pnl,
            "gross_pnl": trade.result.gross_pnl,
            "brokerage": trade.result.brokerage,
            "funding": trade.result.funding,
            "slippage": trade.result.slippage,
            "net_pnl": trade.result.net_pnl,
        }

    def append(self, trade: CashFutureReplayTrade) -> None:
        self.ledger.append(LedgerRecord(self.run_id, "CASH_FUTURE_TRADE", trade.exit_timestamp_ns, self._payload(trade)))

    def append_batch(self, trades: Iterable[CashFutureReplayTrade]) -> int:
        return self.ledger.append_batch(
            LedgerRecord(self.run_id, "CASH_FUTURE_TRADE", trade.exit_timestamp_ns, self._payload(trade))
            for trade in trades
        )
