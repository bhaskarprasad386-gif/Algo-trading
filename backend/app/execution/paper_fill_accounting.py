"""Paper-order adapter for executed-fill P&L accounting.

This module keeps route-level persistence concerns separate from the deterministic
executed-fill accounting core. Only confirmed paper fills should be passed here.
"""

from __future__ import annotations

from app.execution.fill_accounting import ExecutedFill, FillAccountingState, apply_executed_fill


def account_paper_fill(
    *,
    side: str,
    price: float,
    quantity: float,
    current_quantity: float,
    current_average_price: float,
    current_realized_pnl: float = 0.0,
) -> tuple[FillAccountingState, float]:
    """Apply one confirmed paper fill and return new state plus realized delta.

    The caller is responsible for persisting the returned state atomically.
    No order intent or non-filled status is accepted by this adapter.
    """
    before = FillAccountingState(
        quantity=float(current_quantity),
        average_price=float(current_average_price),
        realized_pnl=float(current_realized_pnl),
    )
    after = apply_executed_fill(
        before,
        ExecutedFill(side=side, price=price, quantity=quantity),
    )
    realized_delta = round(after.realized_pnl - before.realized_pnl, 8)
    return after, realized_delta
