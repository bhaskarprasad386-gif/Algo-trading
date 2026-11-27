"""Box Spread payoff construction from executable entry quotes."""

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
    low = _quote(event, "low")
    high = _quote(event, "high")
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


def build_strategy_payoff_legs(strategy_id: str, event: Mapping[str, Any], *, direction: str | None = None) -> tuple[PayoffLeg, ...]:
    if strategy_id != "box-spread":
        raise ValueError(f"unsupported arbitrage strategy: {strategy_id}")
    return box_payoff_legs(event, **({} if direction is None else {"direction": direction}))


__all__ = ["box_payoff_legs", "build_strategy_payoff_legs"]
