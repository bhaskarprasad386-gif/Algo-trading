"""Executable expiry-calendar spread primitives for independent backtests.

Calendar legs always retain their distinct expiries. No contract is rolled or
fabricated implicitly; callers must provide executable bid/ask quotes for both
contracts at the same observation timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from .arbitrage_backtester import LiquidityPolicy


@dataclass(frozen=True)
class CalendarQuote:
    timestamp_ns: int
    underlying: str
    expiry: int
    bid: float
    ask: float
    lot_size: int = 1
    instrument_class: Literal["STOCK", "INDEX"] = "STOCK"
    strike: float | None = None
    option_type: Literal["CALL", "PUT"] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("calendar timestamp_ns must be a non-negative integer")
        if not self.underlying.strip():
            raise ValueError("calendar underlying is required")
        if isinstance(self.expiry, bool) or not isinstance(self.expiry, int) or self.expiry <= 0:
            raise ValueError("calendar expiry must be a positive integer")
        if isinstance(self.lot_size, bool) or not isinstance(self.lot_size, int) or self.lot_size <= 0:
            raise ValueError("calendar lot_size must be a positive integer")
        if self.instrument_class not in {"STOCK", "INDEX"}:
            raise ValueError("invalid calendar instrument_class")
        if self.strike is not None and (isinstance(self.strike, bool) or not isfinite(float(self.strike)) or self.strike <= 0):
            raise ValueError("calendar strike must be finite and positive")
        if self.option_type not in {None, "CALL", "PUT"}:
            raise ValueError("invalid calendar option_type")
        if not isfinite(float(self.bid)) or not isfinite(float(self.ask)):
            raise ValueError("calendar quotes must be finite")
        if self.bid < 0 or self.ask < self.bid:
            raise ValueError("calendar quotes must have valid executable bid/ask")


@dataclass(frozen=True)
class CalendarOpportunity:
    direction: Literal["LONG_NEAR_SHORT_FAR", "SHORT_NEAR_LONG_FAR"]
    timestamp_ns: int
    underlying: str
    near_expiry: int
    far_expiry: int
    strike: float | None
    option_type: Literal["CALL", "PUT"] | None
    executable_edge: float
    edge_per_lot: float
    gross_pnl: float


class CalendarSpreadBacktester:
    """Evaluate executable near/far calendar spreads."""

    @staticmethod
    def evaluate(
        near: CalendarQuote,
        far: CalendarQuote,
        *,
        direction: Literal["LONG_NEAR_SHORT_FAR", "SHORT_NEAR_LONG_FAR"] =
        "LONG_NEAR_SHORT_FAR",
        fees_per_unit: float = 0.0,
        liquidity: LiquidityPolicy | None = None,
    ) -> CalendarOpportunity | None:
        if direction not in {"LONG_NEAR_SHORT_FAR", "SHORT_NEAR_LONG_FAR"}:
            raise ValueError("invalid calendar direction")
        if near.underlying != far.underlying:
            raise ValueError("calendar legs must share underlying")
        if near.expiry >= far.expiry:
            raise ValueError("near expiry must be earlier than far expiry")
        if near.timestamp_ns != far.timestamp_ns:
            raise ValueError("calendar legs must share timestamp")
        if near.lot_size != far.lot_size or near.lot_size <= 0:
            raise ValueError("calendar legs must share a positive lot size")
        if near.instrument_class != far.instrument_class:
            raise ValueError("calendar legs must share instrument class")
        if near.strike != far.strike or near.option_type != far.option_type:
            raise ValueError("calendar option legs must share strike and type")
        if liquidity is not None:
            if liquidity.min_option_volume > 0 or liquidity.min_option_oi > 0 or liquidity.max_spread_pct is not None:
                # CalendarQuote has no depth fields; non-zero option liquidity requirements cannot be proven.
                raise ValueError("calendar liquidity requires depth fields on both legs")
        if not isfinite(float(fees_per_unit)) or fees_per_unit < 0:
            raise ValueError("fees_per_unit must be finite and non-negative")

        if direction == "LONG_NEAR_SHORT_FAR":
            edge = far.bid - near.ask - fees_per_unit
        else:
            edge = near.bid - far.ask - fees_per_unit
        if not isfinite(edge):
            raise ValueError("calendar executable edge must be finite")
        if edge <= 0:
            return None
        pnl = edge * near.lot_size
        return CalendarOpportunity(
            direction=direction,
            timestamp_ns=near.timestamp_ns,
            underlying=near.underlying,
            near_expiry=near.expiry,
            far_expiry=far.expiry,
            strike=near.strike,
            option_type=near.option_type,
            executable_edge=edge,
            edge_per_lot=pnl,
            gross_pnl=pnl,
        )


__all__ = ["CalendarQuote", "CalendarOpportunity", "CalendarSpreadBacktester"]
