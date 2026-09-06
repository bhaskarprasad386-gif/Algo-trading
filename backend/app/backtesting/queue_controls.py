"""Order cancel/reinsert controls backed by queue-generation state."""

from app.backtesting.execution import ExecutionSide, OrderType, SimOrder
from app.backtesting.order_lifecycle import OrderStatus
from app.backtesting.queue_lifecycle import QueueLifecycleState


def cancel_order(engine, order_id: str, timestamp_ns: int, reason: str = "cancelled") -> None:
    """Cancel an open engine order and retire its queue position."""
    lifecycle = engine._order_lifecycles.get(order_id)
    order = engine._open_orders.get(order_id)
    if lifecycle is None or order is None:
        raise KeyError(f"open order not found: {order_id}")
    lifecycle.cancel(timestamp_ns, reason)
    queue = engine._queue_lifecycles.get(order_id)
    if queue is None:
        queue = QueueLifecycleState(engine._dynamic_queue_ahead.get(order_id, order.queue_ahead_quantity))
    engine._queue_lifecycles[order_id] = queue.cancel()
    if engine.portfolio is not None:
        engine.portfolio.release_margin(order_id)
    engine._reserved_margin.pop(order_id, None)
    engine._open_orders.pop(order_id, None)
    engine._dynamic_queue_ahead.pop(order_id, None)


def reinsert_order(engine, order_id: str, new_order_id: str, timestamp_ns: int, queue_ahead_quantity: int) -> SimOrder:
    """Cancel/replace an open order and start a fresh queue generation."""
    lifecycle = engine._order_lifecycles.get(order_id)
    old_order = engine._open_orders.get(order_id)
    if lifecycle is None or old_order is None:
        raise KeyError(f"open order not found: {order_id}")
    if lifecycle.state.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
        raise ValueError("only open orders can be reinserted")
    remaining = lifecycle.state.remaining_quantity
    if remaining <= 0:
        raise ValueError("cannot reinsert a fully filled order")
    replacement = SimOrder(
        order_id=new_order_id,
        instrument=old_order.instrument,
        side=old_order.side,
        quantity=remaining,
        order_type=old_order.order_type,
        limit_price=old_order.limit_price,
        stop_price=old_order.stop_price,
        submitted_at_ns=timestamp_ns,
        queue_ahead_quantity=queue_ahead_quantity,
        time_in_force=old_order.time_in_force,
    )
    lifecycle.replace(replacement, timestamp_ns, "cancel/reinsert")
    queue = engine._queue_lifecycles.get(order_id)
    if queue is None:
        queue = QueueLifecycleState(engine._dynamic_queue_ahead.get(order_id, old_order.queue_ahead_quantity))
    new_queue = queue.cancel().reinsert(queue_ahead_quantity)
    reservation = engine._reserved_margin.pop(order_id, 0.0)
    if engine.portfolio is not None and reservation:
        engine.portfolio.release_margin(order_id)
    engine._open_orders.pop(order_id, None)
    engine._dynamic_queue_ahead.pop(order_id, None)
    engine._queue_lifecycles.pop(order_id, None)

    new_lifecycle = engine._lifecycle(replacement, timestamp_ns)
    engine._open_orders[new_order_id] = replacement
    engine._dynamic_queue_ahead[new_order_id] = new_queue.queue_ahead_quantity
    engine._queue_lifecycles[new_order_id] = new_queue
    if reservation:
        engine._reserved_margin[new_order_id] = reservation
        if engine.portfolio is not None:
            engine.portfolio.reserve_margin(new_order_id, reservation)
    return replacement
