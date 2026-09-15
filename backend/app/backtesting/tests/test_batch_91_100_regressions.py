from app.backtesting.engine import BacktestConfig, BacktestEngine, EventSignal
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.execution import ExecutionSimulator, SimOrder, ExecutionSide, OrderBook, DepthLevel
from app.backtesting.high_resolution_streaming import HighResolutionStreamingRunner

def rec(ts, seq, price):
    return HistoricalRecord(source="test", instrument="X", timeframe="tick", timestamp_ns=ts, payload={"price": price}, sequence=seq)

def test_event_hold_updates_final_mark_and_duplicate_is_rejected():
    engine = BacktestEngine(BacktestConfig(quantity=1))
    events = [rec(1, 1, 100), rec(2, 2, 110), rec(3, 3, 90)]
    def strategy(ctx):
        return EventSignal("BUY") if ctx.timestamp_ns == 1 else EventSignal("HOLD")
    result = engine.run_events(events, strategy)
    assert result.has_open_trade and result.unrealized_pnl == -10
    try:
        engine.run_events([rec(1, 1, 100), rec(1, 1, 101)], lambda _: EventSignal("HOLD"))
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate event identity was accepted")

def test_depth_updates_reject_backwards_time():
    sim = ExecutionSimulator()
    order = SimOrder("o", "X", ExecutionSide.BUY, 1)
    book = OrderBook(asks=(DepthLevel(100, 1),))
    try:
        sim.execute_depth_updates(order, [(2, book, ()), (1, book, ())])
    except ValueError as exc:
        assert "ordered" in str(exc)
    else:
        raise AssertionError("out-of-order depth updates were accepted")

def test_high_resolution_rejects_fractional_quantity():
    class Bad: side = "BUY"; quantity = 1.5
    from app.backtesting.universal import MarketEvent
    event = MarketEvent(1, "X", "trade", {"price": 100}, 1)
    try:
        HighResolutionStreamingRunner._fill_from_signal(Bad(), event)
    except ValueError as exc:
        assert "quantity" in str(exc)
    else:
        raise AssertionError("fractional quantity was silently truncated")
