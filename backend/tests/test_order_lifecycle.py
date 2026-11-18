from app.backtesting.execution import ExecutionSide, SimFill, SimOrder
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus


def test_partial_then_full_fill_tracks_average_price():
    order = SimOrder("O1", "NIFTY", ExecutionSide.BUY, 10)
    lifecycle = OrderLifecycle(order)

    assert lifecycle.state.status == OrderStatus.SUBMITTED
    lifecycle.accept()
    state = lifecycle.apply_fill(SimFill("O1", "NIFTY", ExecutionSide.BUY, 4, 100.0, 1))
    assert state.status == OrderStatus.PARTIALLY_FILLED
    assert state.filled_quantity == 4

    state = lifecycle.apply_fill(SimFill("O1", "NIFTY", ExecutionSide.BUY, 6, 110.0, 2))
    assert state.status == OrderStatus.FILLED
    assert state.filled_quantity == 10
    assert state.average_fill_price == 106.0


def test_reject_and_cancel_are_terminal():
    rejected = OrderLifecycle(SimOrder("O2", "NIFTY", ExecutionSide.BUY, 1))
    rejected.reject("insufficient margin")
    assert rejected.state.status == OrderStatus.REJECTED

    cancelled = OrderLifecycle(SimOrder("O3", "NIFTY", ExecutionSide.SELL, 1))
    cancelled.accept()
    cancelled.cancel()
    assert cancelled.state.status == OrderStatus.CANCELLED


def test_invalid_transition_and_overfill_are_rejected():
    lifecycle = OrderLifecycle(SimOrder("O4", "NIFTY", ExecutionSide.BUY, 5))
    try:
        lifecycle.apply_fill(SimFill("O4", "NIFTY", ExecutionSide.BUY, 1, 100.0, 1))
        assert False
    except ValueError as exc:
        assert "accepted" in str(exc)

    lifecycle.accept()
    lifecycle.apply_fill(SimFill("O4", "NIFTY", ExecutionSide.BUY, 5, 100.0, 1))
    try:
        lifecycle.cancel()
        assert False
    except ValueError as exc:
        assert "terminal" in str(exc)
