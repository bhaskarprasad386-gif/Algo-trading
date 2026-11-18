"""Normalize scanner/executed strategy inputs into explicit payoff legs.

This module deliberately does not invent option legs.  A payoff can only include
legs for which an entry/fill price and quantity are explicitly supplied.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.execution.payoff import PayoffLeg


@dataclass(frozen=True)
class StrategyLegInput:
    kind: str
    side: str
    entry_price: float
    quantity: float
    strike: float | None = None
    multiplier: float = 1.0


def build_strategy_legs(inputs: tuple[StrategyLegInput, ...]) -> tuple[PayoffLeg, ...]:
    """Convert normalized strategy-leg inputs into deterministic payoff legs."""
    if not inputs:
        raise ValueError("strategy must contain at least one leg")
    return tuple(
        PayoffLeg(
            kind=item.kind.upper(),
            side=item.side.upper(),
            strike=item.strike,
            entry_price=item.entry_price,
            quantity=item.quantity,
            multiplier=item.multiplier,
        )
        for item in inputs
    )


def build_cash_future_strategy(
    *,
    cash_entry_price: float,
    future_entry_price: float,
    quantity: float,
    multiplier: float = 1.0,
) -> tuple[PayoffLeg, ...]:
    """Build the explicit cash/future legs used by the scanner bridge.

    The scanner's executable cash/future opportunity is represented as a long
    cash leg plus a short future leg.  This is a strategy description only;
    actual P&L still requires executed fills and live marks.
    """
    if future_entry_price <= cash_entry_price:
        raise ValueError("future entry price must exceed cash entry price")
    return build_strategy_legs(
        (
            StrategyLegInput("SPOT", "BUY", cash_entry_price, quantity, multiplier=multiplier),
            StrategyLegInput("FUTURE", "SELL", future_entry_price, quantity, multiplier=multiplier),
        )
    )
