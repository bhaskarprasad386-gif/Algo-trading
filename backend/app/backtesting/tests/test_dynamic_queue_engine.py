from app.backtesting.events import EventType, MarketEvent
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.execution import ExecutionSide, ExecutionSimulator, OrderType, SimOrder
from app.backtesting.portfolio import Portfolio


def _depth(ts, evidence, ask_qty=5):
    return MarketEvent(
        timestamp_ns=ts,
        instrument="NIFTY",
        event_type=EventType.DEPTH,
        payload={"bids": [(99.0, 10)], "asks": [(100.0, ask_qty)], "queue_evidence": evidence},
        sequence=ts,
        source="test",
    )


def test_event_engine_advances_open_order_queue_from_explicit_level_evidence():
    engine = EventBacktestEngine(execution=ExecutionSimulator())
    order = SimOrder("dyn-1", "NIFTY", ExecutionSide.BUY, 2, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=8)
    engine._open_orders[order.order_id] = order
    engine._dynamic_queue_ahead[order.order_id] = 8
    engine._update_market_state(_depth(1_000, [{"price": 100.0, "executed_quantity": 3}]))
    assert engine._dynamic_queue_ahead[order.order_id] == 5
    engine._update_market_state(_depth(2_000, [{"price": 100.0, "cancelled_quantity_ahead": 2}]))
    assert engine._dynamic_queue_ahead[order.order_id] == 3


def test_depth_change_without_queue_evidence_does_not_advance_open_order_queue():
    engine = EventBacktestEngine(execution=ExecutionSimulator())
    order = SimOrder("dyn-2", "NIFTY", ExecutionSide.BUY, 2, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=8)
    engine._open_orders[order.order_id] = order
    engine._dynamic_queue_ahead[order.order_id] = 8
    engine._update_market_state(_depth(1_000, []))
    assert engine._dynamic_queue_ahead[order.order_id] == 8


def test_dynamic_queue_state_is_checkpoint_serializable():
    engine = EventBacktestEngine(execution=ExecutionSimulator())
    order = SimOrder("dyn-3", "NIFTY", ExecutionSide.BUY, 2, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=8)
    engine._open_orders[order.order_id] = order
    engine._dynamic_queue_ahead[order.order_id] = 3
    state = engine.market_state()
    restored = EventBacktestEngine(execution=ExecutionSimulator())
    restored.restore_market_state(state)
    assert restored.market_state()["open_orders"][0]["dynamic_queue_ahead"] == 3


def test_partial_fill_preserves_source_backed_dynamic_queue():
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=Portfolio(initial_cash=1_000_000))
    order = SimOrder("dyn-4", "NIFTY", ExecutionSide.BUY, 4, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=8)
    engine._lifecycle(order, 1)
    engine._open_orders[order.order_id] = order
    engine._dynamic_queue_ahead[order.order_id] = 3

    event = _depth(2, [], ask_qty=5)
    engine._update_market_state(event)
    fills = engine._try_execute_orders((order,), event)

    assert sum(fill.quantity for fill in fills) == 2
    assert order.order_id in engine._open_orders
    assert engine._dynamic_queue_ahead[order.order_id] == 3
    assert engine.order_states[order.order_id].remaining_quantity == 2


def test_cancel_order_closes_current_queue_generation():
    engine = EventBacktestEngine(execution=ExecutionSimulator())
    order = SimOrder("gen-1", "NIFTY", ExecutionSide.BUY, 2, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=8)
    engine._lifecycle(order, 1)
    engine._open_orders[order.order_id] = order
    engine._queue_lifecycles[order.order_id] = __import__("app.backtesting.queue_lifecycle", fromlist=["QueueLifecycleState"]).QueueLifecycleState(8)
    engine._dynamic_queue_ahead[order.order_id] = 8
    engine.cancel_order(order.order_id, 2, "user cancel")
    assert engine.order_states[order.order_id].status.value == "CANCELLED"
    assert engine.queue_states[order.order_id].resting is False
    assert engine.queue_states[order.order_id].generation == 0
    assert order.order_id not in engine.open_orders


def test_replace_order_reinserts_with_new_queue_generation():
    engine = EventBacktestEngine(execution=ExecutionSimulator())
    old = SimOrder("gen-2", "NIFTY", ExecutionSide.BUY, 4, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=8)
    engine._lifecycle(old, 1)
    engine._open_orders[old.order_id] = old
    engine._queue_lifecycles[old.order_id] = __import__("app.backtesting.queue_lifecycle", fromlist=["QueueLifecycleState"]).QueueLifecycleState(3)
    engine._dynamic_queue_ahead[old.order_id] = 3
    replacement = SimOrder("gen-3", "NIFTY", ExecutionSide.BUY, 4, order_type=OrderType.LIMIT, limit_price=100.0, queue_ahead_quantity=12)
    effective = engine.replace_order(old.order_id, replacement, 2, queue_ahead_quantity=12)
    assert engine.order_states[old.order_id].status.value == "REPLACED"
    assert effective.order_id in engine.open_orders
    assert engine.queue_states[old.order_id].resting is False
    assert engine.queue_states[effective.order_id].resting is True
    assert engine.queue_states[effective.order_id].generation == 1
    assert engine.queue_states[effective.order_id].queue_ahead_quantity == 12
