"""Executable expiry-calendar spread primitives for independent backtests.

Calendar legs always retain their distinct expiries. No contract is rolled or
fabricated implicitly; callers must provide executable bid/ask quotes for both
contracts at the same observation timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    """Evaluate executable near/far calendar spreads.

    The returned edge is the executable entry credit/debit advantage between
    the two expiries. Profit at later exit/settlement must be evaluated by the
    caller using the actual later quotes; this primitive deliberately does not
    invent a far-expiry convergence price.
    """

    @staticmethod
    def evaluate(
        near: CalendarQuote,
        far: CalendarQuote,
        *,
        direction: Literal["LONG_NEAR_SHORT_FAR", "SHORT_NEAR_LONG_FAR"] =
        "LONG_NEAR_SHORT_FAR",
        fees_per_unit: float = 0.0,
    ) -> CalendarOpportunity | None:
        if near.underlying != far.underlying:
            raise ValueError("calendar legs must share underlying")
        if near.expiry >= far.expiry:
            raise ValueError("near expiry must be earlier than far expiry")
        if near.timestamp_ns != far.timestamp_ns:
            raise ValueError("calendar legs must share timestamp")
        if near.lot_size != far.lot_size:
            raise ValueError("calendar legs must share lot size")
        if near.instrument_class != far.instrument_class:
            raise ValueError("calendar legs must share instrument class")
        if near.strike != far.strike or near.option_type != far.option_type:
            raise ValueError("calendar option legs must share strike and type")
        if near.bid < 0 or near.ask < near.bid or far.bid < 0 or far.ask < far.bid:
            raise ValueError("calendar quotes must have valid executable bid/ask")
        if fees_per_unit < 0:
            raise ValueError("fees_per_unit must be non-negative")

        if direction == "LONG_NEAR_SHORT_FAR":
            # Buy near at ask, sell far at bid. A positive value means the
            # initial spread can be entered with the configured edge.
            edge = far.bid - near.ask - fees_per_unit
        else:
            edge = near.bid - far.ask - fees_per_unit
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
