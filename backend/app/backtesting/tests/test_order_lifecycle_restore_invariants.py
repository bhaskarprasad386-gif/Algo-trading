import pytest

from app.backtesting.execution import ExecutionSide, SimOrder
from app.backtesting.order_lifecycle import OrderLifecycle


def _state(status: str, filled_quantity: int, events=None):
    order = SimOrder("o1", "SBIN", ExecutionSide.BUY, 10, submitted_at_ns=100)
    return {
        "order": order.export_state(),
        "status": status,
        "filled_quantity": filled_quantity,
        "average_fill_price": 100.0,
        "time_in_force": order.time_in_force.value,
        "events": events or [],
    }


def test_restore_rejects_filled_state_without_full_quantity():
    with pytest.raises(ValueError, match="FILLED lifecycle must have full quantity"):
        OrderLifecycle.restore_state(_state("FILLED", 9))


def test_restore_rejects_event_with_mismatched_remaining_quantity():
    state = _state("PARTIALLY_FILLED", 4, [{
        "order_id": "o1",
        "status": "PARTIALLY_FILLED",
        "timestamp_ns": 120,
        "filled_quantity": 4,
        "remaining_quantity": 5,
    }])
    with pytest.raises(ValueError, match="invalid lifecycle event quantities"):
        OrderLifecycle.restore_state(state)
