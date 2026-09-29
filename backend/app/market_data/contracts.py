"""Canonical strategy-neutral market-data contracts.

Batch H establishes the stable data vocabulary used by the future common
market-data manager. It does not own broker connectivity or strategy logic.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any, Mapping

class InstrumentType(str, Enum):
    EQUITY = "equity"
    INDEX = "index"
    FUTURE = "future"
    OPTION = "option"
    COMMODITY = "commodity"

class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"

@dataclass(frozen=True)
class InstrumentKey:
    """Stable identity for a tradable instrument within a broker feed."""
    exchange: str
    segment: str
    token: str

    def __post_init__(self) -> None:
        for name, value in (("exchange", self.exchange), ("segment", self.segment), ("token", self.token)):
            if not str(value).strip():
                raise ValueError(f"{name} is required")

    @property
    def value(self) -> str:
        return f"{self.exchange.strip()}:{self.segment.strip()}:{self.token.strip()}"

@dataclass(frozen=True)
class MarketDataRecord:
    """Canonical normalized snapshot/event consumed by strategy-neutral layers."""
    instrument: InstrumentKey
    symbol: str
    instrument_type: InstrumentType
    timestamp_ns: int
    timeframe: str = "1s"
    ltp: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    volume: int | None = None
    oi: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    underlying: str | None = None
    expiry: str | None = None
    strike: float | None = None
    option_type: OptionType | None = None
    lot_size: int | None = None
    tick_size: float | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentKey):
            raise TypeError("instrument must be an InstrumentKey")
        if not str(self.symbol).strip():
            raise ValueError("symbol is required")
        if not isinstance(self.instrument_type, InstrumentType):
            raise TypeError("instrument_type must be an InstrumentType")
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be a non-negative integer")
        if not str(self.timeframe).strip():
            raise ValueError("timeframe is required")
        for name in ("ltp", "bid", "ask", "bid_qty", "ask_qty", "high", "low", "open", "close", "strike", "tick_size"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(float(value))):
                raise ValueError(f"{name} must be finite when provided")
        for name in ("volume", "oi", "lot_size"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer when provided")
        if self.option_type is not None and not isinstance(self.option_type, OptionType):
            raise TypeError("option_type must be an OptionType")
        if self.instrument_type is InstrumentType.OPTION:
            if self.strike is None or self.option_type is None:
                raise ValueError("option instruments require strike and option_type")
        elif self.option_type is not None:
            raise ValueError("option_type is only valid for option instruments")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")
        if self.tick_size is not None and self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.lot_size is not None and self.lot_size <= 0:
            raise ValueError("lot_size must be positive")

    @property
    def identity(self) -> tuple[str, str, str, int]:
        return (self.instrument.exchange.strip(), self.instrument.segment.strip(), self.instrument.token.strip(), self.timestamp_ns)

    @property
    def is_executable_quote(self) -> bool:
        return self.bid is not None and self.ask is not None and self.bid >= 0 and self.ask >= self.bid

    def as_dict(self) -> dict[str, Any]:
        result = {
            "exchange": self.instrument.exchange,
            "segment": self.instrument.segment,
            "token": self.instrument.token,
            "symbol": self.symbol,
            "instrument_type": self.instrument_type.value,
            "timestamp_ns": self.timestamp_ns,
            "timeframe": self.timeframe,
            "ltp": self.ltp,
            "bid": self.bid,
            "ask": self.ask,
            "bid_qty": self.bid_qty,
            "ask_qty": self.ask_qty,
            "volume": self.volume,
            "oi": self.oi,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "underlying": self.underlying,
            "expiry": self.expiry,
            "strike": self.strike,
            "option_type": self.option_type.value if self.option_type else None,
            "lot_size": self.lot_size,
            "tick_size": self.tick_size,
        }
        result["payload"] = dict(self.payload)
        return result
