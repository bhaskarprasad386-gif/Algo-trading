from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.execution import ExecutionSide, ExecutionSimulator, OrderType, SimFill, SimOrder
from app.backtesting.portfolio import Portfolio
from app.backtesting.queue_controls import cancel_order, reinsert_order


def test_cancel_reinsert_starts_new_queue_generation_and_preserves_remaining_quantity():
    portfolio = Portfolio(initial_cash=1_000_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    order = SimOrder("qr-1", "NIFTY", ExecutionSide.BUY, 10, OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=8)
    engine._lifecycle(order, 1)
    engine._open_orders[order.order_id] = order
    engine._dynamic_queue_ahead[order.order_id] = 3
    engine._queue_lifecycles[order.order_id] = __import__("app.backtesting.queue_lifecycle", fromlist=["QueueLifecycleState"]).QueueLifecycleState(3)
    portfolio.reserve_margin(order.order_id, 1000.0)
    engine._reserved_margin[order.order_id] = 1000.0
    engine._order_lifecycles[order.order_id].apply_fill(SimFill("qr-1", "NIFTY", ExecutionSide.BUY, 4, 100.0, 2))

    replacement = reinsert_order(engine, "qr-1", "qr-2", 3, 12)

    assert replacement.quantity == 6
    assert "qr-1" not in engine.open_orders
    assert engine.open_orders["qr-2"].quantity == 6
    assert engine._dynamic_queue_ahead["qr-2"] == 12
    assert engine._queue_lifecycles["qr-2"].generation == 1
    assert engine._queue_lifecycles["qr-2"].resting is True
    assert engine.order_states["qr-1"].status.value == "REPLACED"
    assert engine.order_states["qr-2"].status.value == "ACCEPTED"
    assert portfolio.reserved_margin == 1000.0


def test_cancel_removes_queue_position_and_releases_margin():
    portfolio = Portfolio(initial_cash=1_000_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    order = SimOrder("qr-3", "NIFTY", ExecutionSide.BUY, 2, OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=5)
    engine._lifecycle(order, 1)
    engine._open_orders[order.order_id] = order
    engine._dynamic_queue_ahead[order.order_id] = 5
    portfolio.reserve_margin(order.order_id, 200.0)
    engine._reserved_margin[order.order_id] = 200.0

    cancel_order(engine, "qr-3", 2)

    assert "qr-3" not in engine.open_orders
    assert engine.order_states["qr-3"].status.value == "CANCELLED"
    assert engine._queue_lifecycles["qr-3"].resting is False
    assert portfolio.reserved_margin == 0.0
