"""Strategy-specific multi-leg payoff construction using real entry quotes.

This module only constructs payoff legs from observed executable bid/ask quotes.
It never invents a fill, expiry, strike, or higher-resolution price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.execution.payoff import PayoffLeg


@dataclass(frozen=True)
class StrategyPayoff:
    """A graph-ready payoff definition plus auditable strategy metadata."""

    strategy_id: str
    legs: tuple[PayoffLeg, ...]
    metadata: Mapping[str, Any]


def _side_price(bid: float, ask: float, side: str) -> float:
    if side == "BUY":
        return float(ask)
    if side == "SELL":
        return float(bid)
    raise ValueError("side must be BUY or SELL")


def _validate_quote_prices(*values: float) -> None:
    if any(value < 0 for value in values):
        raise ValueError("quote prices cannot be negative")


def build_box_payoff(event: Mapping[str, Any], *, direction: str = "LONG") -> StrategyPayoff:
    low = event["low"]
    high = event["high"]
    if low["underlying"] != high["underlying"] or low["expiry"] != high["expiry"]:
        raise ValueError("box legs must share underlying and expiry")
    if not float(low["strike"]) < float(high["strike"]):
        raise ValueError("box low strike must be below high strike")
    _validate_quote_prices(low["call_bid"], low["call_ask"], low["put_bid"], low["put_ask"],
                           high["call_bid"], high["call_ask"], high["put_bid"], high["put_ask"])
    qty = float(low.get("lot_size", 1))
    if direction == "LONG":
        legs = (
            PayoffLeg("CALL", "BUY", float(low["strike"]), float(low["call_ask"]), qty),
            PayoffLeg("CALL", "SELL", float(high["strike"]), float(high["call_bid"]), qty),
            PayoffLeg("PUT", "BUY", float(low["strike"]), float(low["put_ask"]), qty),
            PayoffLeg("PUT", "SELL", float(high["strike"]), float(high["put_bid"]), qty),
        )
    elif direction == "SHORT":
        legs = (
            PayoffLeg("CALL", "SELL", float(low["strike"]), float(low["call_bid"]), qty),
            PayoffLeg("CALL", "BUY", float(high["strike"]), float(high["call_ask"]), qty),
            PayoffLeg("PUT", "SELL", float(low["strike"]), float(low["put_bid"]), qty),
            PayoffLeg("PUT", "BUY", float(high["strike"]), float(high["put_ask"]), qty),
        )
    else:
        raise ValueError("direction must be LONG or SHORT")
    return StrategyPayoff("box-spread", legs, {
        "underlying": low["underlying"], "expiry": low["expiry"],
        "low_strike": float(low["strike"]), "high_strike": float(high["strike"]),
        "quote_timestamp_ns": low["timestamp_ns"], "direction": direction,
    })


def build_synthetic_cash_carry_payoff(
    event: Mapping[str, Any], *, direction: str = "LONG", carry_factor: float = 1.0
) -> StrategyPayoff:
    option = event["option"]
    future = event["future"]
    if option["underlying"] != future["underlying"] or option["expiry"] != future["expiry"]:
        raise ValueError("synthetic legs must share underlying and expiry")
    if option["timestamp_ns"] != future["timestamp_ns"]:
        raise ValueError("synthetic legs must share timestamp")
    if carry_factor <= 0:
        raise ValueError("carry_factor must be positive")
    _validate_quote_prices(option["call_bid"], option["call_ask"], option["put_bid"], option["put_ask"],
                           future["bid"], future["ask"])
    qty = float(future.get("lot_size", 1))
    strike = float(option["strike"])
    if direction == "LONG":
        legs = (
            PayoffLeg("CALL", "BUY", strike, float(option["call_ask"]), qty),
            PayoffLeg("PUT", "SELL", strike, float(option["put_bid"]), qty),
            PayoffLeg("FUTURE", "SELL", None, float(future["bid"]), qty),
        )
    elif direction == "SHORT":
        legs = (
            PayoffLeg("CALL", "SELL", strike, float(option["call_bid"]), qty),
            PayoffLeg("PUT", "BUY", strike, float(option["put_ask"]), qty),
            PayoffLeg("FUTURE", "BUY", None, float(future["ask"]), qty),
        )
    else:
        raise ValueError("direction must be LONG or SHORT")
    return StrategyPayoff("synthetic-cash-carry", legs, {
        "underlying": option["underlying"], "expiry": option["expiry"],
        "strike": strike, "quote_timestamp_ns": option["timestamp_ns"],
        "direction": direction, "carry_factor": float(carry_factor),
    })


def build_cash_future_payoff(event: Mapping[str, Any], *, direction: str = "LONG_CASH_SHORT_FUTURE") -> StrategyPayoff:
    quote = event["cash_future"]
    _validate_quote_prices(quote["spot_bid"], quote["spot_ask"], quote["future_bid"], quote["future_ask"])
    if quote["spot_ask"] < quote["spot_bid"] or quote["future_ask"] < quote["future_bid"]:
        raise ValueError("invalid cash/future quote")
    qty = float(quote.get("lot_size", 1))
    if direction == "LONG_CASH_SHORT_FUTURE":
        legs = (
            PayoffLeg("SPOT", "BUY", None, float(quote["spot_ask"]), qty),
            PayoffLeg("FUTURE", "SELL", None, float(quote["future_bid"]), qty),
        )
    elif direction == "SHORT_CASH_LONG_FUTURE":
        legs = (
            PayoffLeg("SPOT", "SELL", None, float(quote["spot_bid"]), qty),
            PayoffLeg("FUTURE", "BUY", None, float(quote["future_ask"]), qty),
        )
    else:
        raise ValueError("unsupported cash-future direction")
    return StrategyPayoff("cash-future", legs, {
        "underlying": quote["underlying"], "expiry": quote["expiry"],
        "quote_timestamp_ns": quote["timestamp_ns"], "direction": direction,
        "carry_factor": float(quote.get("carry_factor", 1.0)),
    })


def build_calendar_payoff(event: Mapping[str, Any], *, direction: str = "LONG_NEAR_SHORT_FAR") -> StrategyPayoff:
    near = event["near"]
    far = event["far"]
    if near["underlying"] != far["underlying"]:
        raise ValueError("calendar legs must share underlying")
    if near["expiry"] >= far["expiry"]:
        raise ValueError("near expiry must be earlier than far expiry")
    if near["timestamp_ns"] != far["timestamp_ns"]:
        raise ValueError("calendar legs must share timestamp")
    if near.get("strike") != far.get("strike") or near.get("option_type") != far.get("option_type"):
        raise ValueError("calendar legs must share strike and option type")
    _validate_quote_prices(near["bid"], near["ask"], far["bid"], far["ask"])
    qty = float(near.get("lot_size", 1))
    kind = str(near.get("option_type") or "FUTURE").upper()
    if kind not in {"CALL", "PUT", "FUTURE"}:
        raise ValueError("unsupported calendar option type")
    strike = None if kind == "FUTURE" else float(near["strike"])
    if direction == "LONG_NEAR_SHORT_FAR":
        legs = (
            PayoffLeg(kind, "BUY", strike, float(near["ask"]), qty),
            PayoffLeg(kind, "SELL", strike, float(far["bid"]), qty),
        )
    elif direction == "SHORT_NEAR_LONG_FAR":
        legs = (
            PayoffLeg(kind, "SELL", strike, float(near["bid"]), qty),
            PayoffLeg(kind, "BUY", strike, float(far["ask"]), qty),
        )
    else:
        raise ValueError("unsupported calendar direction")
    return StrategyPayoff("calendar-spread", legs, {
        "underlying": near["underlying"], "near_expiry": near["expiry"],
        "far_expiry": far["expiry"], "strike": strike,
        "option_type": near.get("option_type"), "quote_timestamp_ns": near["timestamp_ns"],
        "direction": direction,
    })


def build_strategy_payoff(strategy_id: str, event: Mapping[str, Any], *, direction: str | None = None) -> StrategyPayoff:
    """Build payoff legs from the strategy's actual entry quote payload."""
    if strategy_id == "box-spread":
        return build_box_payoff(event, direction=direction or "LONG")
    if strategy_id == "synthetic-cash-carry":
        return build_synthetic_cash_carry_payoff(event, direction=direction or "LONG")
    if strategy_id == "cash-future":
        return build_cash_future_payoff(event, direction=direction or "LONG_CASH_SHORT_FUTURE")
    if strategy_id == "calendar-spread":
        return build_calendar_payoff(event, direction=direction or "LONG_NEAR_SHORT_FAR")
    raise ValueError(f"unsupported arbitrage strategy: {strategy_id}")


__all__ = [
    "StrategyPayoff", "build_box_payoff", "build_synthetic_cash_carry_payoff",
    "build_cash_future_payoff", "build_calendar_payoff", "build_strategy_payoff",
]
