from app.backtesting.events import EventType, MarketEvent
from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.execution import ExecutionSide, ExecutionSimulator, OrderType, SimOrder


def _depth(ts, evidence):
    return MarketEvent(
        timestamp_ns=ts,
        instrument="NIFTY",
        event_type=EventType.DEPTH,
        payload={"bids": [(99.0, 10)], "asks": [(100.0, 5)], "queue_evidence": evidence},
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
