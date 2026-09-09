"""Deterministic forced-liquidation planning for maintenance-margin breaches."""

from typing import Mapping

from app.backtesting.execution import ExecutionSide, SimOrder
from app.backtesting.portfolio import Portfolio
from app.backtesting.risk_controls import evaluate_market_risk


def build_liquidation_orders(
    portfolio: Portfolio,
    marks: Mapping[str, float] | None = None,
    *,
    order_id_prefix: str = "liquidation",
) -> tuple[SimOrder, ...]:
    """Build risk-reducing market orders that flatten marked positions.

    The planner is deterministic and non-mutating. It only emits orders when the
    marked portfolio is in a maintenance-margin call. Positions without a valid
    positive mark are omitted because they cannot be safely priced/executed at the
    current event. Each generated order strictly reduces the corresponding position.
    """
    state = evaluate_market_risk(portfolio, dict(marks or {}))
    if not state.margin_call:
        return ()

    observed_marks = dict(marks or {})
    orders: list[SimOrder] = []
    for position in sorted(state_positions := portfolio.snapshot(observed_marks).positions, key=lambda p: p.instrument):
        if position.quantity == 0:
            continue
        mark = observed_marks.get(position.instrument)
        if not isinstance(mark, (int, float)) or mark <= 0:
            continue
        side = ExecutionSide.SELL if position.quantity > 0 else ExecutionSide.BUY
        orders.append(
            SimOrder(
                order_id=f"{order_id_prefix}:{position.instrument}",
                instrument=position.instrument,
                side=side,
                quantity=abs(position.quantity),
            )
        )
    return tuple(orders)
