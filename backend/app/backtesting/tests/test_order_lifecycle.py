import pytest

from app.backtesting.execution import ExecutionSide, OrderType, SimOrder
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus, TimeInForce, stop_triggered, tif_after_execution


def make_order(order_id="o1", quantity=10, side=ExecutionSide.BUY, submitted_at_ns=100):
    return SimOrder(order_id, "NIFTY", side, quantity, submitted_at_ns=submitted_at_ns)


def test_partial_fill_then_fill_records_full_lifecycle():
    lifecycle = OrderLifecycle(make_order())
    assert lifecycle.accept(100).status == OrderStatus.ACCEPTED
    assert lifecycle.apply_fill(lifecycle.to_fill(100.0, 110, 4)).status == OrderStatus.PARTIALLY_FILLED
    state = lifecycle.apply_fill(lifecycle.to_fill(101.0, 120, 6))
    assert state.status == OrderStatus.FILLED
    assert state.remaining_quantity == 0
    assert state.average_fill_price == pytest.approx(100.6)
    assert [event.status for event in state.events] == [OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED]


def test_cancel_and_expire_are_terminal():
    cancelled = OrderLifecycle(make_order("o2"))
    cancelled.accept(100)
    assert cancelled.cancel(150).status == OrderStatus.CANCELLED
    with pytest.raises(ValueError):
        cancelled.cancel(160)

    expired = OrderLifecycle(make_order("o3"))
    expired.accept(100)
    assert expired.expire(200).status == OrderStatus.EXPIRED


def test_replace_requires_same_instrument_and_side_and_is_terminal():
    lifecycle = OrderLifecycle(make_order("old"))
    lifecycle.accept(100)
    replacement = make_order("new", quantity=5, submitted_at_ns=150)
    state = lifecycle.replace(replacement, 150)
    assert state.status == OrderStatus.REPLACED
    assert state.events[-1].replacement_order_id == "new"
    with pytest.raises(ValueError):
        lifecycle.replace(replacement, 160)


def test_ioc_and_fok_residual_actions_are_deterministic():
    assert tif_after_execution(TimeInForce.IOC, 0) == OrderStatus.FILLED
    assert tif_after_execution(TimeInForce.IOC, 2) == OrderStatus.CANCELLED
    assert tif_after_execution(TimeInForce.FOK, 2) == OrderStatus.REJECTED
    assert tif_after_execution(TimeInForce.DAY, 2) is None


def test_stop_trigger_uses_observed_price_only():
    buy = SimOrder("stop-buy", "NIFTY", ExecutionSide.BUY, 1, OrderType.STOP, stop_price=101.0)
    sell = SimOrder("stop-sell", "NIFTY", ExecutionSide.SELL, 1, OrderType.STOP, stop_price=99.0)
    assert not stop_triggered(buy, 100.99)
    assert stop_triggered(buy, 101.0)
    assert not stop_triggered(sell, 99.01)
    assert stop_triggered(sell, 99.0)


def test_reject_is_only_allowed_before_acceptance():
    lifecycle = OrderLifecycle(make_order("reject"))
    state = lifecycle.reject("risk limit", 100)
    assert state.status == OrderStatus.REJECTED
    assert state.reject_reason == "risk limit"
    with pytest.raises(ValueError):
        lifecycle.accept(101)


def test_lifecycle_checkpoint_round_trip_preserves_partial_order_and_history():
    lifecycle = OrderLifecycle(make_order("resume", quantity=10))
    lifecycle.accept(100)
    lifecycle.apply_fill(lifecycle.to_fill(100.0, 110, 4))

    restored = OrderLifecycle.restore_state(lifecycle.export_state())

    assert restored.state.order == lifecycle.state.order
    assert restored.state.status == OrderStatus.PARTIALLY_FILLED
    assert restored.state.filled_quantity == 4
    assert restored.state.remaining_quantity == 6
    assert restored.state.average_fill_price == pytest.approx(100.0)
    assert restored.state.events == lifecycle.state.events
    assert restored.to_fill(101.0, 120, 6).quantity == 6
