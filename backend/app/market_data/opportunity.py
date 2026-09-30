"""Common executable opportunity primitives for strategy scanners.

This module is strategy-neutral: it does not open broker connections, persist data,
or decide a strategy's formula. Strategies provide their own opportunity gap and
use these primitives for quote-side selection, validation, gross-profit sizing,
and threshold qualification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Mapping

from .contracts import MarketDataRecord


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


def executable_price(record: MarketDataRecord, side: OrderSide) -> float:
    """Return the price that would be executable for the requested order side."""
    if not isinstance(record, MarketDataRecord):
        raise TypeError("record must be a MarketDataRecord")
    if not isinstance(side, OrderSide):
        raise TypeError("side must be an OrderSide")
    if record.bid is None or record.ask is None:
        raise ValueError("executable bid/ask quote is required")
    if record.bid < 0 or record.ask < record.bid:
        raise ValueError("invalid executable quote")
    price = record.ask if side is OrderSide.BUY else record.bid
    if not isfinite(float(price)):
        raise ValueError("executable price must be finite")
    return float(price)


def validate_opportunity_legs(
    legs: tuple["OpportunityLeg", ...],
    *,
    max_timestamp_skew_ns: int = 0,
) -> None:
    """Reject missing/crossed/stale leg quotes before an opportunity qualifies."""
    if not legs:
        raise ValueError("at least one opportunity leg is required")
    if max_timestamp_skew_ns < 0:
        raise ValueError("max_timestamp_skew_ns cannot be negative")
    timestamps = {leg.record.timestamp_ns for leg in legs}
    if max_timestamp_skew_ns == 0:
        if len(timestamps) != 1:
            raise ValueError("opportunity legs must share the same timestamp")
    else:
        if max(timestamps) - min(timestamps) > max_timestamp_skew_ns:
            raise ValueError("opportunity legs exceed allowed timestamp skew")
    for leg in legs:
        executable_price(leg.record, leg.side)


def gross_profit_from_points(points: float, lot_size: int, lots: int = 1) -> float:
    """Convert executable opportunity points into gross rupee profit."""
    if not isfinite(float(points)):
        raise ValueError("points must be finite")
    if points < 0:
        raise ValueError("points cannot be negative")
    if not isinstance(lot_size, int) or isinstance(lot_size, bool) or lot_size <= 0:
        raise ValueError("lot_size must be a positive integer")
    if not isinstance(lots, int) or isinstance(lots, bool) or lots <= 0:
        raise ValueError("lots must be a positive integer")
    return float(points) * lot_size * lots


def qualifies_opportunity(
    *,
    gap_points: float,
    gross_profit: float,
    minimum_gap_points: float = 0.0,
    minimum_gross_profit: float = 0.0,
) -> bool:
    """Apply inclusive strategy thresholds before a scanner emits an opportunity."""
    values = (
        gap_points,
        gross_profit,
        minimum_gap_points,
        minimum_gross_profit,
    )
    if any(not isfinite(float(value)) for value in values):
        raise ValueError("opportunity values and thresholds must be finite")
    if gap_points < 0 or gross_profit < 0:
        raise ValueError("gap_points and gross_profit cannot be negative")
    if minimum_gap_points < 0 or minimum_gross_profit < 0:
        raise ValueError("minimum thresholds cannot be negative")
    return gap_points >= minimum_gap_points and gross_profit >= minimum_gross_profit


@dataclass(frozen=True)
class OpportunityLeg:
    record: MarketDataRecord
    side: OrderSide
    label: str

    def __post_init__(self) -> None:
        if not isinstance(self.record, MarketDataRecord):
            raise TypeError("record must be a MarketDataRecord")
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be an OrderSide")
        if not str(self.label).strip():
            raise ValueError("leg label is required")

    @property
    def executable_price(self) -> float:
        return executable_price(self.record, self.side)


@dataclass(frozen=True)
class OpportunitySignal:
    """Strategy-neutral qualified/unqualified opportunity snapshot."""
    strategy_id: str
    opportunity_type: str
    symbol: str
    timestamp_ns: int
    gap_points: float
    gross_profit: float
    lot_size: int
    qualifies: bool
    minimum_gap_points: float = 0.0
    minimum_gross_profit: float = 0.0
    legs: tuple[OpportunityLeg, ...] = ()
    expiry: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.opportunity_type.strip() or not self.symbol.strip():
            raise ValueError("strategy_id, opportunity_type and symbol are required")
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be a non-negative integer")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        if not isfinite(float(self.gap_points)) or self.gap_points < 0:
            raise ValueError("gap_points must be finite and non-negative")
        if not isfinite(float(self.gross_profit)) or self.gross_profit < 0:
            raise ValueError("gross_profit must be finite and non-negative")
        expected = qualifies_opportunity(
            gap_points=self.gap_points,
            gross_profit=self.gross_profit,
            minimum_gap_points=self.minimum_gap_points,
            minimum_gross_profit=self.minimum_gross_profit,
        )
        if self.qualifies is not expected:
            raise ValueError("qualifies does not match the configured thresholds")
        if self.legs:
            validate_opportunity_legs(self.legs)

    @property
    def identity(self) -> tuple[str, str, str, int]:
        return (self.strategy_id.strip().lower(), self.opportunity_type.strip().lower(), self.symbol.strip(), self.timestamp_ns)

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "opportunity_type": self.opportunity_type,
            "symbol": self.symbol,
            "timestamp_ns": self.timestamp_ns,
            "gap_points": self.gap_points,
            "gross_profit": self.gross_profit,
            "lot_size": self.lot_size,
            "qualifies": self.qualifies,
            "minimum_gap_points": self.minimum_gap_points,
            "minimum_gross_profit": self.minimum_gross_profit,
            "expiry": self.expiry,
            "legs": [
                {
                    "label": leg.label,
                    "side": leg.side.value,
                    "price": leg.executable_price,
                    "identity": leg.record.identity,
                }
                for leg in self.legs
            ],
            "metadata": dict(self.metadata),
        }


__all__ = [
    "OrderSide",
    "OpportunityLeg",
    "OpportunitySignal",
    "executable_price",
    "validate_opportunity_legs",
    "gross_profit_from_points",
    "qualifies_opportunity",
]
