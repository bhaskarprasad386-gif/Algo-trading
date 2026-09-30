"""Streaming replay of saved live MarketDataRecord-compatible SQLite data.

Replay reads persisted records incrementally; it never downloads historical data
and never materializes an entire day into memory.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable
from app.backtesting.historical_catalog import HistoricalRecord
from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord, OptionType

@dataclass(frozen=True)
class ReplayEvent:
    record: MarketDataRecord
    source_record: HistoricalRecord

class MarketDataReplay:
    def __init__(self, records: Iterable[HistoricalRecord]) -> None:
        self._records = records

    @staticmethod
    def to_market_record(row: HistoricalRecord) -> MarketDataRecord:
        p = dict(row.payload)
        exchange = str(p.get("exchange") or "").strip()
        segment = str(p.get("segment") or exchange).strip()
        token = str(p.get("token") or "").strip()
        symbol = str(p.get("symbol") or row.instrument).strip()
        kind = str(p.get("instrument_type") or "equity").strip().lower()
        try:
            instrument_type = InstrumentType(kind)
        except ValueError:
            instrument_type = InstrumentType.COMMODITY if "COMMODITY" in kind else InstrumentType.FUTURE if "FUTURE" in kind else InstrumentType.EQUITY
        option = p.get("option_type")
        option_type = OptionType(str(option).upper()) if option else None
        return MarketDataRecord(
            instrument=InstrumentKey(exchange, segment, token),
            symbol=symbol,
            instrument_type=instrument_type,
            timestamp_ns=int(p.get("timestamp_ns") or row.timestamp_ns),
            timeframe=row.timeframe,
            ltp=p.get("ltp"), bid=p.get("bid"), ask=p.get("ask"),
            bid_qty=p.get("bid_qty"), ask_qty=p.get("ask_qty"),
            volume=p.get("volume"), oi=p.get("oi"),
            open=p.get("open"), high=p.get("high"), low=p.get("low"), close=p.get("close"),
            underlying=p.get("underlying"), expiry=p.get("expiry"), strike=p.get("strike"),
            option_type=option_type, lot_size=p.get("lot_size"), tick_size=p.get("tick_size"),
            payload=p,
        )

    def stream(self, callback: Callable[[ReplayEvent], None]) -> int:
        if not callable(callback):
            raise TypeError("callback must be callable")
        count = 0
        for row in self._records:
            callback(ReplayEvent(self.to_market_record(row), row))
            count += 1
        return count

__all__ = ["MarketDataReplay", "ReplayEvent"]
