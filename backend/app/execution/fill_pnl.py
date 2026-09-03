"""Executed-fill based P&L primitives.

Signals and submitted orders never affect realized P&L. Only executed fills do.
The reducer is deterministic and keeps the state small enough for live/paper use.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutedFill:
    side: str
    price: float
    quantity: float
    fill_id: str = ""

    def __post_init__(self) -> None:
        if self.side.upper() not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.price <= 0 or self.quantity <= 0:
            raise ValueError("price and quantity must be positive")


@dataclass(frozen=True)
class FillPnlState:
    quantity: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0


def apply_executed_fill(state: FillPnlState, fill: ExecutedFill) -> FillPnlState:
    """Apply one executed fill using FIFO-equivalent average-cost accounting.

    This reducer supports a long position and rejects sells larger than the
    currently executed long quantity. Pending/rejected/cancelled orders are
    represented by no fill and therefore cannot change the state.
    """
    side = fill.side.upper()
    qty = float(fill.quantity)
    price = float(fill.price)

    if side == "BUY":
        new_qty = state.quantity + qty
        new_avg = ((state.quantity * state.average_price) + (qty * price)) / new_qty
        return FillPnlState(new_qty, round(new_avg, 8), round(state.realized_pnl, 8))

    if qty > state.quantity + 1e-12:
        raise ValueError("sell fill exceeds executed long quantity")
    pnl = (price - state.average_price) * qty
    remaining = state.quantity - qty
    new_avg = state.average_price if remaining > 1e-12 else 0.0
    return FillPnlState(round(max(remaining, 0.0), 8), round(new_avg, 8), round(state.realized_pnl + pnl, 8))
