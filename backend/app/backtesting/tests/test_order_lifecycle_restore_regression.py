import pytest

from app.backtesting.execution import ExecutionSide, OrderType, SimOrder, TimeInForce
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus


def _order() -> SimOrder:
    return SimOrder(
        order_id="restore-1",
        instrument="NIFTY",
        side=ExecutionSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        submitted_at_ns=100,
        time_in_force=TimeInForce.DAY,
    )


def _state(events, status, filled_quantity=0, average_fill_price=0.0):
    order = _order()
    return {
        "order": {
            "order_id": order.order_id,
            "instrument": order.instrument,
            "side": order.side.value,
            "quantity": order.quantity,
            "order_type": order.order_type.value,
            "limit_price": order.limit_price,
            "stop_price": order.stop_price,
            "submitted_at_ns": order.submitted_at_ns,
            "queue_ahead_quantity": order.queue_ahead_quantity,
            "time_in_force": order.time_in_force.value,
        },
        "status": status.value,
        "filled_quantity": filled_quantity,
        "average_fill_price": average_fill_price,
        "reject_reason": "rejected" if status == OrderStatus.REJECTED else None,
        "time_in_force": order.time_in_force.value,
        "events": events,
    }


def _event(status, timestamp, filled=0, remaining=10, reason=None, replacement_order_id=None):
    return {
        "order_id": "restore-1",
        "status": status.value,
        "timestamp_ns": timestamp,
        "filled_quantity": filled,
        "remaining_quantity": remaining,
        "reason": reason,
        "replacement_order_id": replacement_order_id,
    }


def test_restore_rejects_terminal_to_filled_transition():
    raw = _state(
        [
            _event(OrderStatus.ACCEPTED, 100),
            _event(OrderStatus.CANCELLED, 110),
            _event(OrderStatus.FILLED, 120, filled=10, remaining=0),
        ],
        OrderStatus.FILLED,
        filled_quantity=10,
        average_fill_price=100.0,
    )
    with pytest.raises(ValueError, match="illegal lifecycle transition"):
        OrderLifecycle.restore_state(raw)


def test_restore_accepts_valid_partial_fill_sequence():
    raw = _state(
        [
            _event(OrderStatus.ACCEPTED, 100),
            _event(OrderStatus.PARTIALLY_FILLED, 110, filled=4, remaining=6),
            _event(OrderStatus.FILLED, 120, filled=10, remaining=0),
        ],
        OrderStatus.FILLED,
        filled_quantity=10,
        average_fill_price=100.5,
    )
    restored = OrderLifecycle.restore_state(raw)
    assert restored.state.status == OrderStatus.FILLED
    assert restored.state.filled_quantity == 10
