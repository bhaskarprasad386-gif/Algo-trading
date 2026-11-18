"""Deterministic executed-fill accounting shared by paper/live execution."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ExecutedFill:
    """A broker-confirmed execution; order intent is never accepted here."""

    side: str
    price: float
    quantity: float
    fill_id: str = ""

    def __post_init__(self) -> None:
        if self.side.upper() not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if not math.isfinite(float(self.price)) or not math.isfinite(float(self.quantity)):
            raise ValueError("price and quantity must be finite")
        if self.price <= 0 or self.quantity <= 0:
            raise ValueError("price and quantity must be positive")
        if self.fill_id is not None and not isinstance(self.fill_id, str):
            raise ValueError("fill_id must be a string")


@dataclass(frozen=True)
class FillAccountingState:
    """Signed position state using average entry cost and realized P&L."""

    quantity: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0
    applied_fill_ids: tuple[str, ...] = ()


def apply_executed_fill(state: FillAccountingState, fill: ExecutedFill) -> FillAccountingState:
    """Apply one confirmed fill, including long/short reversals.

    A BUY increases the signed position; a SELL decreases it. Closing fills
    realize P&L against the existing average entry price. Crossing through
    zero opens the remainder at the crossing fill price. A non-empty fill_id
    is applied at most once so broker/replay retries cannot double count it.
    """
    if fill.fill_id and fill.fill_id in state.applied_fill_ids:
        return state

    signed_fill = fill.quantity if fill.side.upper() == "BUY" else -fill.quantity
    current = state.quantity

    if current == 0 or (current > 0 and signed_fill > 0) or (current < 0 and signed_fill < 0):
        new_qty = current + signed_fill
        if current == 0:
            new_avg = fill.price
        else:
            new_avg = ((abs(current) * state.average_price) + (abs(signed_fill) * fill.price)) / abs(new_qty)
        realized = round(state.realized_pnl, 8)
    else:
        closing_qty = min(abs(current), abs(signed_fill))
        if current > 0:
            pnl = (fill.price - state.average_price) * closing_qty
        else:
            pnl = (state.average_price - fill.price) * closing_qty

        new_qty = current + signed_fill
        if new_qty == 0:
            new_avg = 0.0
        elif (current > 0 and new_qty > 0) or (current < 0 and new_qty < 0):
            new_avg = state.average_price
        else:
            new_avg = fill.price
        realized = round(state.realized_pnl + pnl, 8)

    applied = state.applied_fill_ids
    if fill.fill_id:
        applied = applied + (fill.fill_id,)

    return FillAccountingState(round(new_qty, 8), round(new_avg, 8), realized, applied)
