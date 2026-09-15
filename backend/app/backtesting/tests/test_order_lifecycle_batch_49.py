import pytest

from app.backtesting.execution import ExecutionSide, SimFill, SimOrder
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus


def _state():
    order = SimOrder("restore", "NIFTY", ExecutionSide.BUY, 5, submitted_at_ns=10)
    lifecycle = OrderLifecycle(order)
    lifecycle.accept(10)
    lifecycle.apply_fill(SimFill("restore", "NIFTY", ExecutionSide.BUY, 2, 100.0, 11))
    return lifecycle.export_state()


def test_restore_rejects_state_that_disagrees_with_final_event_status():
    raw = _state()
    raw["status"] = OrderStatus.FILLED.value
    with pytest.raises(ValueError, match="status does not match final event"):
        OrderLifecycle.restore_state(raw)


def test_restore_rejects_state_that_disagrees_with_final_event_quantity():
    raw = _state()
    raw["filled_quantity"] = 3
    with pytest.raises(ValueError, match="quantities do not match final event"):
        OrderLifecycle.restore_state(raw)


def test_restore_rejects_non_submitted_state_without_history():
    raw = _state()
    raw["status"] = OrderStatus.ACCEPTED.value
    raw["events"] = []
    with pytest.raises(ValueError, match="must contain event history"):
        OrderLifecycle.restore_state(raw)


def test_restore_rejects_mismatched_lifecycle_tif():
    raw = _state()
    raw["time_in_force"] = "IOC"
    with pytest.raises(ValueError, match="time_in_force does not match order"):
        OrderLifecycle.restore_state(raw)


def test_restore_round_trip_preserves_consistent_state():
    raw = _state()
    restored = OrderLifecycle.restore_state(raw)
    assert restored.export_state() == raw
