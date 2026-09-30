"""Cash-Future opportunity scanner over the common normalized market-data contract.

This module is strategy logic only: it consumes MarketDataRecord values supplied by
CommonWebSocketManager and never opens a broker connection.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Iterable

from app.market_data.contracts import InstrumentType, MarketDataRecord
from app.market_data.opportunity import (
    OpportunityLeg,
    OpportunitySignal,
    OrderSide,
    gross_profit_from_points,
    qualifies_opportunity,
)


@dataclass(frozen=True)
class CashFutureScanResult:
    signal: OpportunitySignal
    cash: MarketDataRecord
    future: MarketDataRecord
    contract_month: str

    def as_dict(self) -> dict[str, object]:
        data = self.signal.as_dict()
        data.update({
            "date_time": datetime.fromtimestamp(self.signal.timestamp_ns / 1_000_000_000).isoformat(),
            "symbol": self.signal.symbol,
            "contract_month": self.contract_month,
            "cash_ltp": self.cash.ltp,
            "future_ltp": self.future.ltp,
            "cash_bid": self.cash.bid,
            "cash_ask": self.cash.ask,
            "future_bid": self.future.bid,
            "future_ask": self.future.ask,
            "cash_bid_qty": self.cash.bid_qty,
            "cash_ask_qty": self.cash.ask_qty,
            "future_bid_qty": self.future.bid_qty,
            "future_ask_qty": self.future.ask_qty,
            "lot_size": self.signal.lot_size,
            "gap": self.signal.gap_points,
            "gross_profit": self.signal.gross_profit,
            "executable": self.signal.qualifies,
            "live_orders": False,
            "paper_trade": True,
        })
        return data


class CashFutureOpportunityScanner:
    """Pair same-timestamp cash/current/near futures from normalized common data."""

    strategy_id = "cash-future"

    def __init__(self, *, minimum_gap_points: float = 0.0, minimum_gross_profit: float = 0.0) -> None:
        if minimum_gap_points < 0 or minimum_gross_profit < 0:
            raise ValueError("thresholds cannot be negative")
        self.minimum_gap_points = float(minimum_gap_points)
        self.minimum_gross_profit = float(minimum_gross_profit)
        self._lock = RLock()
        self._latest: dict[str, dict[str, MarketDataRecord]] = {}
        self._results: dict[tuple[str, str], CashFutureScanResult] = {}

    @staticmethod
    def _validate_record(record: MarketDataRecord) -> None:
        if record.instrument_type not in {InstrumentType.EQUITY, InstrumentType.FUTURE}:
            raise ValueError("Cash-Future accepts only equity cash and future records")
        if not record.is_executable_quote:
            raise ValueError("Cash-Future requires a valid two-sided executable quote")
        if record.timestamp_ns < 1:
            raise ValueError("record timestamp must be positive")

    def update(self, record: MarketDataRecord, *, contract_month: str) -> CashFutureScanResult | None:
        self._validate_record(record)
        month = str(contract_month).strip().upper()
        if record.instrument_type is InstrumentType.EQUITY:
            leg = "CASH"
        else:
            if month not in {"CURRENT", "NEAR"}:
                raise ValueError("contract_month must be CURRENT or NEAR")
            leg = month
        symbol = (record.underlying or record.symbol).strip().upper()
        with self._lock:
            bucket = self._latest.setdefault(symbol, {})
            bucket[leg] = record
            cash = bucket.get("CASH")
            future = bucket.get(month) if leg != "CASH" else bucket.get("CURRENT") or bucket.get("NEAR")
            if cash is None or future is None:
                return None
            if cash.timestamp_ns != future.timestamp_ns:
                return None
            if future.lot_size is None or future.lot_size <= 0:
                return None
            gap = float(future.bid) - float(cash.ask)
            if gap < 0:
                return None
            gross = gross_profit_from_points(gap, int(future.lot_size))
            qualifies = qualifies_opportunity(
                gap_points=gap,
                gross_profit=gross,
                minimum_gap_points=self.minimum_gap_points,
                minimum_gross_profit=self.minimum_gross_profit,
            )
            signal = OpportunitySignal(
                strategy_id=self.strategy_id,
                opportunity_type="cash-future",
                symbol=symbol,
                timestamp_ns=record.timestamp_ns,
                gap_points=gap,
                gross_profit=gross,
                lot_size=int(future.lot_size),
                qualifies=qualifies,
                minimum_gap_points=self.minimum_gap_points,
                minimum_gross_profit=self.minimum_gross_profit,
                legs=(
                    OpportunityLeg(cash, OrderSide.BUY, "cash-entry"),
                    OpportunityLeg(future, OrderSide.SELL, f"future-{month.lower()}-entry"),
                ),
                expiry=future.expiry,
                metadata={"contract_month": month, "source": "common-market-data", "live_orders": False},
            )
            result = CashFutureScanResult(signal, cash, future, month)
            self._results[(symbol, month)] = result
            return result

    def scan(self, records: Iterable[tuple[MarketDataRecord, str]]) -> tuple[CashFutureScanResult, ...]:
        results = []
        for record, month in records:
            result = self.update(record, contract_month=month)
            if result is not None and result.signal.qualifies:
                results.append(result)
        return tuple(sorted(results, key=lambda item: item.signal.gross_profit, reverse=True))

    def snapshot(self, *, qualified_only: bool = True) -> tuple[CashFutureScanResult, ...]:
        with self._lock:
            values = tuple(self._results.values())
        if qualified_only:
            values = tuple(item for item in values if item.signal.qualifies)
        return tuple(sorted(values, key=lambda item: item.signal.gross_profit, reverse=True))


__all__ = ["CashFutureOpportunityScanner", "CashFutureScanResult"]
