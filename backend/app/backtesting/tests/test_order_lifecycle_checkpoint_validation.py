import pytest

from app.backtesting.execution import ExecutionSide, SimOrder
from app.backtesting.order_lifecycle import OrderLifecycle


def _state(quantity=2, submitted_at_ns=100, filled_quantity=0):
    order = SimOrder("o1", "NIFTY", ExecutionSide.BUY, quantity, submitted_at_ns=submitted_at_ns)
    lifecycle = OrderLifecycle(order)
    return {
        "order": lifecycle.export_state()["order"],
        "status": "ACCEPTED",
        "filled_quantity": filled_quantity,
        "average_fill_price": 0.0,
        "reject_reason": None,
        "time_in_force": "DAY",
        "events": [],
    }


def test_restore_rejects_fractional_order_quantity_without_coercion():
    state = _state()
    state["order"]["quantity"] = 1.5
    with pytest.raises(ValueError, match="invalid lifecycle"):
        OrderLifecycle.restore_state(state)


def test_restore_rejects_fractional_lifecycle_event_quantity_and_timestamp():
    state = _state()
    state["events"] = [{
        "order_id": "o1",
        "status": "ACCEPTED",
        "timestamp_ns": 101.5,
        "filled_quantity": 1.5,
        "remaining_quantity": 0.5,
        "reason": None,
        "replacement_order_id": None,
    }]
    with pytest.raises(ValueError, match="invalid lifecycle"):
        OrderLifecycle.restore_state(state)


def test_restore_accepts_integer_checkpoint_values():
    state = _state(quantity=3, submitted_at_ns=100, filled_quantity=1)
    state["events"] = [{
        "order_id": "o1",
        "status": "ACCEPTED",
        "timestamp_ns": 100,
        "filled_quantity": 0,
        "remaining_quantity": 3,
        "reason": None,
        "replacement_order_id": None,
    }]
    restored = OrderLifecycle.restore_state(state)
    assert restored.state.order.quantity == 3
    assert restored.state.filled_quantity == 1
