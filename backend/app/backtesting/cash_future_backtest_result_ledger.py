"""Durable ledger adapter for Cash-Future convergence backtest results."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping

from .ledger import BacktestLedger, LedgerRecord

RECORD_TYPE = "CASH_FUTURE_CONVERGENCE_TRADE"


class CashFutureBacktestResultLedger:
    """Persist completed convergence trades without changing the backtest result API."""

    def __init__(self, ledger: BacktestLedger, run_id: str) -> None:
        if not run_id.strip():
            raise ValueError("run_id is required")
        self.ledger = ledger
        self.run_id = run_id

    @staticmethod
    def _record(run_id: str, trade: Mapping[str, Any]) -> LedgerRecord:
        required = {
            "entry_time",
            "exit_time",
            "entry_gap",
            "exit_gap",
            "lot_size",
            "gross_profit",
            "charges",
            "funding_cost",
            "net_profit",
            "roi_pct",
            "exit_reason",
        }
        missing = sorted(required.difference(trade))
        if missing:
            raise ValueError(f"cash-future trade fields missing: {', '.join(missing)}")
        timestamp = trade["exit_time"]
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise ValueError("exit_time is required")
        return LedgerRecord(
            run_id=run_id,
            record_type=RECORD_TYPE,
            timestamp_ns=_timestamp_ns(timestamp),
            payload=dict(trade),
        )

    def append(self, trade: Mapping[str, Any]) -> None:
        self.ledger.append(self._record(self.run_id, trade))

    def append_many(self, trades: Iterable[Mapping[str, Any]]) -> int:
        return self.ledger.append_batch(self._record(self.run_id, trade) for trade in trades)


def _timestamp_ns(value: str) -> int:
    from datetime import datetime, timezone

    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    timestamp_ns = int(parsed.timestamp() * 1_000_000_000)
    if timestamp_ns < 0:
        raise ValueError("exit_time cannot be before epoch")
    return timestamp_ns


__all__ = ["CashFutureBacktestResultLedger", "RECORD_TYPE"]
