"""Deterministic multi-leg payoff analytics for paper/live analysis."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PayoffLeg:
    kind: str
    side: str
    strike: float | None
    entry_price: float
    quantity: float
    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.kind.upper() not in {"SPOT", "FUTURE", "CALL", "PUT"}:
            raise ValueError("unsupported leg kind")
        if self.side.upper() not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if not math.isfinite(float(self.entry_price)) or not math.isfinite(float(self.quantity)) or not math.isfinite(float(self.multiplier)):
            raise ValueError("leg values must be finite")
        if self.entry_price < 0 or self.quantity <= 0 or self.multiplier <= 0:
            raise ValueError("invalid leg values")
        if self.kind.upper() in {"CALL", "PUT"} and (self.strike is None or not math.isfinite(float(self.strike)) or self.strike <= 0):
            raise ValueError("option legs require a positive finite strike")
        if self.kind.upper() in {"SPOT", "FUTURE"} and self.strike is not None:
            raise ValueError("spot/future legs cannot have a strike")


def _leg_pnl(leg: PayoffLeg, underlying_price: float) -> float:
    kind = leg.kind.upper()
    sign = 1.0 if leg.side.upper() == "BUY" else -1.0
    if kind == "SPOT":
        intrinsic = underlying_price
    elif kind == "FUTURE":
        intrinsic = underlying_price
    elif kind == "CALL":
        intrinsic = max(underlying_price - float(leg.strike), 0.0)
    else:
        intrinsic = max(float(leg.strike) - underlying_price, 0.0)
    return sign * (intrinsic - leg.entry_price) * leg.quantity * leg.multiplier


def payoff_at_price(legs: tuple[PayoffLeg, ...], underlying_price: float) -> float:
    if not math.isfinite(float(underlying_price)):
        raise ValueError("underlying_price must be finite")
    if underlying_price < 0:
        raise ValueError("underlying_price cannot be negative")
    return round(sum(_leg_pnl(leg, underlying_price) for leg in legs), 8)


def payoff_curve(legs: tuple[PayoffLeg, ...], prices: tuple[float, ...]) -> tuple[float, ...]:
    if not prices:
        return ()
    if any(not math.isfinite(float(price)) or price < 0 for price in prices):
        raise ValueError("payoff curve prices must be finite and non-negative")
    return tuple(payoff_at_price(legs, price) for price in prices)


def break_even_points(legs: tuple[PayoffLeg, ...], prices: tuple[float, ...]) -> list[float]:
    if len(prices) < 2:
        return []
    values = payoff_curve(legs, prices)
    points: list[float] = []
    for left, right, pnl_left, pnl_right in zip(prices, prices[1:], values, values[1:]):
        if pnl_left == 0:
            points.append(left)
        if pnl_left * pnl_right < 0:
            ratio = abs(pnl_left) / (abs(pnl_left) + abs(pnl_right))
            points.append(round(left + (right - left) * ratio, 8))
        elif pnl_right == 0:
            points.append(right)
    return list(dict.fromkeys(points))


def payoff_summary(legs: tuple[PayoffLeg, ...], prices: tuple[float, ...]) -> dict:
    values = payoff_curve(legs, prices)
    if not values:
        return {"max_profit": None, "max_loss": None, "break_even_points": [], "prices": [], "pnl": []}
    return {
        "max_profit": max(values),
        "max_loss": min(values),
        "break_even_points": break_even_points(legs, prices),
        "prices": list(prices),
        "pnl": list(values),
    }
