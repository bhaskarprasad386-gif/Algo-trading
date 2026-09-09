"""Generic payoff-curve primitives for independent backtest results.

The curve is deliberately strategy-agnostic: option legs, futures, cash/future
arbitrage and future strategies can all expose terminal P&L against an underlying
price without changing the result-ledger schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class PayoffLeg:
    """One terminal payoff leg."""

    kind: str
    side: str
    strike: float | None
    quantity: float
    entry_price: float
    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.side.strip():
            raise ValueError("kind and side are required")
        if self.quantity <= 0 or self.multiplier <= 0:
            raise ValueError("quantity and multiplier must be positive")
        if self.entry_price < 0:
            raise ValueError("entry_price must be non-negative")

    def payoff(self, underlying_price: float) -> float:
        if underlying_price < 0:
            raise ValueError("underlying_price must be non-negative")
        side = self.side.upper()
        sign = 1.0 if side in {"BUY", "LONG"} else -1.0 if side in {"SELL", "SHORT"} else None
        if sign is None:
            raise ValueError(f"unsupported side: {self.side}")
        kind = self.kind.upper()
        if kind in {"CALL", "C"}:
            intrinsic = max(underlying_price - (self.strike or 0.0), 0.0)
        elif kind in {"PUT", "P"}:
            intrinsic = max((self.strike or 0.0) - underlying_price, 0.0)
        elif kind in {"FUTURE", "FUT", "CASH", "STOCK"}:
            intrinsic = underlying_price
        else:
            raise ValueError(f"unsupported payoff kind: {self.kind}")
        return sign * (intrinsic - self.entry_price) * self.quantity * self.multiplier


@dataclass(frozen=True)
class PayoffPoint:
    underlying_price: float
    pnl: float


@dataclass(frozen=True)
class PayoffCurve:
    points: tuple[PayoffPoint, ...]
    currency: str = "INR"

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("payoff curve requires at least one point")
        if any(p.underlying_price < 0 for p in self.points):
            raise ValueError("underlying prices must be non-negative")

    def as_rows(self) -> list[Mapping[str, float]]:
        return [{"underlying_price": p.underlying_price, "pnl": p.pnl} for p in self.points]


def build_payoff_curve(
    legs: Iterable[PayoffLeg],
    underlying_prices: Iterable[float],
    *,
    currency: str = "INR",
) -> PayoffCurve:
    """Generate a deterministic terminal payoff curve for one backtest run."""
    leg_list = tuple(legs)
    if not leg_list:
        raise ValueError("at least one payoff leg is required")
    prices = tuple(float(p) for p in underlying_prices)
    if not prices:
        raise ValueError("at least one underlying price is required")
    points = tuple(
        PayoffPoint(price, sum(leg.payoff(price) for leg in leg_list))
        for price in prices
    )
    return PayoffCurve(points=points, currency=currency)
