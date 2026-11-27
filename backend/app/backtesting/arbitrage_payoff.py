"""Box Spread payoff construction using observed executable entry quotes."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from app.execution.payoff import PayoffLeg


@dataclass(frozen=True)
class StrategyPayoff:
    """A graph-ready payoff definition plus auditable strategy metadata."""

    strategy_id: str
    legs: tuple[PayoffLeg, ...]
    metadata: Mapping[str, Any]


def _validate_quote_prices(*values: float) -> None:
    numeric = [float(value) for value in values]
    if any(not math.isfinite(value) or value < 0 for value in numeric):
        raise ValueError("quote prices must be finite and non-negative")


def _validate_quantity(value: Any) -> float:
    quantity = float(value)
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("lot_size must be finite and positive")
    return quantity


def build_box_payoff(event: Mapping[str, Any], *, direction: str = "LONG") -> StrategyPayoff:
    low = event["low"]
    high = event["high"]
    if low["underlying"] != high["underlying"] or low["expiry"] != high["expiry"]:
        raise ValueError("box legs must share underlying and expiry")
    low_strike = float(low["strike"])
    high_strike = float(high["strike"])
    if not math.isfinite(low_strike) or not math.isfinite(high_strike) or not low_strike < high_strike:
        raise ValueError("box strikes must be finite and low strike must be below high strike")
    _validate_quote_prices(
        low["call_bid"], low["call_ask"], low["put_bid"], low["put_ask"],
        high["call_bid"], high["call_ask"], high["put_bid"], high["put_ask"],
    )
    qty = _validate_quantity(low.get("lot_size", 1))
    if direction == "LONG":
        legs = (
            PayoffLeg("CALL", "BUY", low_strike, float(low["call_ask"]), qty),
            PayoffLeg("CALL", "SELL", high_strike, float(high["call_bid"]), qty),
            PayoffLeg("PUT", "BUY", high_strike, float(high["put_ask"]), qty),
            PayoffLeg("PUT", "SELL", low_strike, float(low["put_bid"]), qty),
        )
    elif direction == "SHORT":
        legs = (
            PayoffLeg("CALL", "SELL", low_strike, float(low["call_bid"]), qty),
            PayoffLeg("CALL", "BUY", high_strike, float(high["call_ask"]), qty),
            PayoffLeg("PUT", "SELL", high_strike, float(high["put_bid"]), qty),
            PayoffLeg("PUT", "BUY", low_strike, float(low["put_ask"]), qty),
        )
    else:
        raise ValueError("direction must be LONG or SHORT")
    return StrategyPayoff("box-spread", legs, {
        "underlying": low["underlying"], "expiry": low["expiry"],
        "low_strike": low_strike, "high_strike": high_strike,
        "quote_timestamp_ns": low["timestamp_ns"], "direction": direction,
    })


def build_strategy_payoff(strategy_id: str, event: Mapping[str, Any], *, direction: str | None = None) -> StrategyPayoff:
    """Build payoff legs from the retained Box Spread entry quote payload."""
    if strategy_id != "box-spread":
        raise ValueError(f"unsupported arbitrage strategy: {strategy_id}")
    return build_box_payoff(event, direction=direction or "LONG")


__all__ = ["StrategyPayoff", "build_box_payoff", "build_strategy_payoff"]
