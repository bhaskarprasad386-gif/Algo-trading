"""Deterministic forced-liquidation planning for maintenance-margin breaches."""

from typing import Mapping

from app.backtesting.execution import ExecutionResult, ExecutionSide, ExecutionSimulator, SimOrder
from app.backtesting.portfolio import Portfolio


def _valid_mark(mark: object) -> bool:
    return isinstance(mark, (int, float)) and not isinstance(mark, bool) and mark > 0


def _open_positions(portfolio: Portfolio) -> tuple[tuple[str, int], ...]:
    raw = portfolio.export_state().get("positions", ())
    return tuple((str(item["instrument"]), int(item["quantity"])) for item in raw if int(item["quantity"]) != 0)


def _marked_margin_call(portfolio: Portfolio, observed_marks: Mapping[str, float]) -> bool:
    """Maintenance-margin call using only positions that have a valid mark.

    Unmarked positions are omitted from this view so the planner can still flatten
    executable names instead of fail-closed on a missing mark.
    """
    cash = float(portfolio.export_state()["cash"])
    gross = net = 0.0
    for instrument, quantity in _open_positions(portfolio):
        mark = observed_marks.get(instrument)
        if not _valid_mark(mark):
            continue
        notional = quantity * float(mark)
        gross += abs(notional)
        net += notional
    equity = cash + net
    maintenance = gross * portfolio.risk_config.maintenance_margin_rate
    return equity + 1e-9 < maintenance


def _order_reduces_position(portfolio: Portfolio, order: SimOrder) -> bool:
    current = next((quantity for instrument, quantity in _open_positions(portfolio) if instrument == order.instrument), 0)
    if current == 0:
        return False
    signed = order.quantity if order.side == ExecutionSide.BUY else -order.quantity
    projected = current + signed
    return abs(projected) < abs(current) and (current * projected >= 0 or projected == 0)


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
    observed_marks = dict(marks or {})
    if not _marked_margin_call(portfolio, observed_marks):
        return ()

    orders: list[SimOrder] = []
    for instrument, quantity in sorted(_open_positions(portfolio), key=lambda item: item[0]):
        mark = observed_marks.get(instrument)
        if not _valid_mark(mark):
            continue
        side = ExecutionSide.SELL if quantity > 0 else ExecutionSide.BUY
        orders.append(
            SimOrder(
                order_id=f"{order_id_prefix}:{instrument}",
                instrument=instrument,
                side=side,
                quantity=abs(quantity),
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
        if not _order_reduces_position(portfolio, order):
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
                for fill in result.fills:
                    portfolio.apply_fill(fill, observed_marks)
            except Exception as exc:
                results.append(ExecutionResult((), outstanding, True, f"liquidation accounting failed: {exc}"))
                continue
            prior_filled[order.order_id] = prior_filled.get(order.order_id, 0) + sum(
                fill.quantity for fill in result.fills
            )
        results.append(result)
    return tuple(results)
