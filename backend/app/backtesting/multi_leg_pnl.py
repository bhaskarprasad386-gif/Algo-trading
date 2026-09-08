"""Basket-level P&L accounting for generic multi-leg strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.backtesting.execution import ExecutionSide, SimFill


@dataclass(frozen=True)
class BasketLegPnl:
    leg_id: str
    instrument: str
    side: ExecutionSide
    quantity: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    charges: float
    funding: float = 0.0

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.charges - self.funding


@dataclass(frozen=True)
class BasketPnl:
    signal_id: str
    legs: tuple[BasketLegPnl, ...]

    @property
    def gross_pnl(self) -> float:
        return sum(leg.gross_pnl for leg in self.legs)

    @property
    def charges(self) -> float:
        return sum(leg.charges for leg in self.legs)

    @property
    def funding(self) -> float:
        return sum(leg.funding for leg in self.legs)

    @property
    def net_pnl(self) -> float:
        return sum(leg.net_pnl for leg in self.legs)


def build_basket_pnl(
    signal_id: str,
    entries: Mapping[str, SimFill],
    exits: Mapping[str, SimFill],
    *,
    charges_rate: float = 0.0,
    funding_by_leg: Mapping[str, float] | None = None,
) -> BasketPnl:
    """Match legs by order id and calculate signed basket P&L.

    A basket cannot be valued partially: every entry must have its corresponding
    exit. Charges are applied to both entry and exit notional.
    """
    if charges_rate < 0:
        raise ValueError("charges_rate cannot be negative")
    if set(entries) != set(exits):
        raise ValueError("basket entries and exits must contain the same legs")
    funding_by_leg = funding_by_leg or {}
    legs: list[BasketLegPnl] = []
    for order_id in sorted(entries):
        entry = entries[order_id]
        exit = exits[order_id]
        if entry.instrument != exit.instrument or entry.side != exit.side:
            raise ValueError(f"entry/exit mismatch for {order_id}")
        if entry.quantity != exit.quantity:
            raise ValueError(f"quantity mismatch for {order_id}")
        direction = 1.0 if entry.side == ExecutionSide.BUY else -1.0
        gross = (exit.price - entry.price) * entry.quantity * direction
        charges = (entry.price + exit.price) * entry.quantity * charges_rate
        leg_id = order_id.rsplit(":", 1)[-1]
        legs.append(BasketLegPnl(leg_id, entry.instrument, entry.side, entry.quantity,
                                 entry.price, exit.price, gross, charges,
                                 float(funding_by_leg.get(leg_id, 0.0))))
    return BasketPnl(signal_id, tuple(legs))
