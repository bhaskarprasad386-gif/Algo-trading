"""Deterministic forced-liquidation planning for maintenance-margin breaches."""

from typing import Mapping

from app.backtesting.execution import ExecutionResult, ExecutionSide, ExecutionSimulator, SimOrder
from app.backtesting.portfolio import Portfolio
from app.backtesting.risk_controls import evaluate_market_risk, order_reduces_position_risk


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
    current event.
    """
    state = evaluate_market_risk(portfolio, dict(marks or {}))
    if not state.margin_call:
        return ()

    observed_marks = dict(marks or {})
    orders: list[SimOrder] = []
    for position in sorted(portfolio.snapshot(observed_marks).positions, key=lambda p: p.instrument):
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


def execute_liquidation_orders(
    portfolio: Portfolio,
    orders: tuple[SimOrder, ...],
    simulator: ExecutionSimulator,
    marks: Mapping[str, float],
    timestamp_ns: int,
    *,
    books: Mapping[str, object] | None = None,
) -> tuple[ExecutionResult, ...]:
    """Execute forced orders with retry protection and failure isolation.

    Execution and portfolio application are both fail-closed: an execution or
    accounting exception becomes a rejected result for that order and does not
    mutate the portfolio. A later order can still be attempted. Partial fills are
    committed atomically, and a retry is reduced to the still-outstanding quantity.
    """
    observed_marks = dict(marks)
    prior_filled: dict[str, int] = {}
    for trade in portfolio.trades:
        prior_filled[trade.order_id] = prior_filled.get(trade.order_id, 0) + trade.quantity

    results: list[ExecutionResult] = []
    for order in orders:
        if not order_reduces_position_risk(portfolio, order):
            results.append(ExecutionResult((), order.quantity, True, "order is not risk-reducing"))
            continue

        outstanding = max(0, order.quantity - prior_filled.get(order.order_id, 0))
        if outstanding == 0:
            results.append(ExecutionResult((), 0, False, "already fully liquidated"))
            continue

        effective = SimOrder(
            order_id=order.order_id,
            instrument=order.instrument,
            side=order.side,
            quantity=outstanding,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            submitted_at_ns=order.submitted_at_ns,
            queue_ahead_quantity=order.queue_ahead_quantity,
            time_in_force=order.time_in_force,
        )

        try:
            if books is not None and order.instrument in books:
                result = simulator.execute_depth(effective, books[order.instrument], timestamp_ns)  # type: ignore[arg-type]
            else:
                mark = observed_marks.get(order.instrument)
                if not isinstance(mark, (int, float)) or mark <= 0:
                    results.append(ExecutionResult((), outstanding, True, "missing or invalid liquidation mark"))
                    continue
                result = ExecutionResult((simulator.execute(effective, mark, timestamp_ns),), 0, False, None)
        except Exception as exc:
            results.append(ExecutionResult((), outstanding, True, f"liquidation execution failed: {exc}"))
            continue

        if result.fills:
            try:
                portfolio.apply_fills_atomic(result.fills, observed_marks)
            except Exception as exc:
                results.append(ExecutionResult((), outstanding, True, f"liquidation accounting failed: {exc}"))
                continue
            prior_filled[order.order_id] = prior_filled.get(order.order_id, 0) + sum(
                fill.quantity for fill in result.fills
            )
        results.append(result)
    return tuple(results)
