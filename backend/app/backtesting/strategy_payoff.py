"""Strategy-specific multi-leg payoff construction from executable entry quotes.

The builders preserve the actual entry-side bid/ask used by the strategy. They
never invent an exit or settlement price; payoff analytics are independent of
historical trade closure and are evaluated only across caller-supplied prices.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.execution.payoff import PayoffLeg


def _quote(event: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = event.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} quote payload is required")
    return value


def _leg(kind: str, side: str, entry_price: float, quantity: float, *, strike: float | None = None,
         multiplier: float = 1.0) -> PayoffLeg:
    return PayoffLeg(kind=kind, side=side, strike=strike, entry_price=float(entry_price),
                     quantity=float(quantity), multiplier=float(multiplier))


def box_payoff_legs(event: Mapping[str, Any], *, direction: str = "LONG") -> tuple[PayoffLeg, ...]:
    low = _quote(event, "low"); high = _quote(event, "high")
    quantity = float(low.get("lot_size", 1))
    if float(low["strike"]) >= float(high["strike"]):
        raise ValueError("box low strike must be below high strike")
    if direction == "LONG":
        return (
            _leg("CALL", "BUY", low["call_ask"], 1, strike=low["strike"], multiplier=quantity),
            _leg("CALL", "SELL", high["call_bid"], 1, strike=high["strike"], multiplier=quantity),
            _leg("PUT", "BUY", low["put_ask"], 1, strike=low["strike"], multiplier=quantity),
            _leg("PUT", "SELL", high["put_bid"], 1, strike=high["strike"], multiplier=quantity),
        )
    if direction == "SHORT":
        return (
            _leg("CALL", "SELL", low["call_bid"], 1, strike=low["strike"], multiplier=quantity),
            _leg("CALL", "BUY", high["call_ask"], 1, strike=high["strike"], multiplier=quantity),
            _leg("PUT", "SELL", low["put_bid"], 1, strike=low["strike"], multiplier=quantity),
            _leg("PUT", "BUY", high["put_ask"], 1, strike=high["strike"], multiplier=quantity),
        )
    raise ValueError("direction must be LONG or SHORT")


def synthetic_cash_carry_payoff_legs(event: Mapping[str, Any], *, direction: str = "LONG") -> tuple[PayoffLeg, ...]:
    option = _quote(event, "option"); future = _quote(event, "future")
    strike = float(option["strike"]); quantity = float(future.get("lot_size", 1))
    if direction == "LONG":
        # Long synthetic future = long call + short put; paired with short future.
        return (
            _leg("CALL", "BUY", option["call_ask"], 1, strike=strike, multiplier=quantity),
            _leg("PUT", "SELL", option["put_bid"], 1, strike=strike, multiplier=quantity),
            _leg("FUTURE", "SELL", future["bid"], quantity),
        )
    if direction == "SHORT":
        # Short synthetic future = short call + long put; paired with long future.
        return (
            _leg("CALL", "SELL", option["call_bid"], 1, strike=strike, multiplier=quantity),
            _leg("PUT", "BUY", option["put_ask"], 1, strike=strike, multiplier=quantity),
            _leg("FUTURE", "BUY", future["ask"], quantity),
        )
    raise ValueError("direction must be LONG or SHORT")


def cash_future_payoff_legs(event: Mapping[str, Any], *, direction: str = "LONG_CASH_SHORT_FUTURE") -> tuple[PayoffLeg, ...]:
    quote = _quote(event, "cash_future"); quantity = float(quote.get("lot_size", 1))
    if direction == "LONG_CASH_SHORT_FUTURE":
        return (
            _leg("SPOT", "BUY", quote["spot_ask"], quantity),
            _leg("FUTURE", "SELL", quote["future_bid"], quantity),
        )
    if direction == "SHORT_CASH_LONG_FUTURE":
        return (
            _leg("SPOT", "SELL", quote["spot_bid"], quantity),
            _leg("FUTURE", "BUY", quote["future_ask"], quantity),
        )
    raise ValueError("invalid cash-future direction")


def calendar_payoff_legs(event: Mapping[str, Any], *, direction: str = "LONG_NEAR_SHORT_FAR") -> tuple[PayoffLeg, ...]:
    near = _quote(event, "near"); far = _quote(event, "far")
    quantity = float(near.get("lot_size", 1))
    strike = near.get("strike")
    option_type = near.get("option_type")
    if near["expiry"] >= far["expiry"]:
        raise ValueError("near expiry must be earlier than far expiry")
    if direction == "LONG_NEAR_SHORT_FAR":
        near_side, far_side = "BUY", "SELL"
        near_price, far_price = near["ask"], far["bid"]
    elif direction == "SHORT_NEAR_LONG_FAR":
        near_side, far_side = "SELL", "BUY"
        near_price, far_price = near["bid"], far["ask"]
    else:
        raise ValueError("invalid calendar direction")
    kind = option_type or "FUTURE"
    return (
        _leg(kind, near_side, near_price, quantity, strike=strike),
        _leg(kind, far_side, far_price, quantity, strike=strike),
    )


def build_strategy_payoff_legs(strategy_id: str, event: Mapping[str, Any], *, direction: str | None = None) -> tuple[PayoffLeg, ...]:
    builders = {
        "box-spread": box_payoff_legs,
        "synthetic-cash-carry": synthetic_cash_carry_payoff_legs,
        "cash-future": cash_future_payoff_legs,
        "calendar-spread": calendar_payoff_legs,
    }
    try:
        builder = builders[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unsupported arbitrage strategy: {strategy_id}") from exc
    return builder(event, **({} if direction is None else {"direction": direction}))


__all__ = [
    "box_payoff_legs", "synthetic_cash_carry_payoff_legs", "cash_future_payoff_legs",
    "calendar_payoff_legs", "build_strategy_payoff_legs",
]
