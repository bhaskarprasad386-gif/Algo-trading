from app.backtesting.engine import EventSignal
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.universal_engine import UniversalEventBacktestEngine


def _event(ts: int, instrument: str, price: float, seq: int) -> HistoricalRecord:
    return HistoricalRecord(
        source="test",
        instrument=instrument,
        timeframe="tick",
        timestamp_ns=ts,
        payload={"price": price},
        sequence=seq,
    )


def test_universal_engine_isolates_multiple_instruments_and_supports_shorts() -> None:
    events = [
        _event(1, "AAA", 100.0, 1),
        _event(2, "BBB", 200.0, 1),
        _event(3, "AAA", 110.0, 1),
        _event(4, "BBB", 190.0, 1),
    ]

    def strategy(ctx):
        if ctx.instrument == "AAA":
            return EventSignal("BUY" if ctx.timestamp_ns == 1 else "SELL")
        return EventSignal("SELL" if ctx.timestamp_ns == 2 else "BUY")

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run(events, strategy)

    assert result.fill_count == 4
    assert result.realized_pnl == 20.0
    assert result.unrealized_pnl == 0.0
    assert result.final_equity == 100_020.0
    assert result.snapshots[-1].positions == ()


def test_universal_strategy_receives_portfolio_snapshot_and_available_margin() -> None:
    seen = []

    def strategy(ctx):
        seen.append((ctx.portfolio_snapshot.equity, ctx.available_margin, ctx.open_orders))
        return EventSignal("HOLD")

    result = UniversalEventBacktestEngine(100_000.0).run(
        [_event(1, "AAA", 100.0, 1)],
        strategy,
    )

    assert result.fill_count == 0
    assert seen == [(100_000.0, 100_000.0, ())]


def test_universal_engine_keeps_positions_independent_by_instrument() -> None:
    events = [
        _event(1, "AAA", 100.0, 1),
        _event(2, "BBB", 200.0, 1),
    ]

    def strategy(ctx):
        return EventSignal("BUY")

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run(events, strategy)

    positions = {position.instrument: position.quantity for position in result.snapshots[-1].positions}
    assert positions == {"AAA": 1, "BBB": 1}


def test_universal_engine_ioc_partial_fill_cancels_remainder() -> None:
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder, TimeInForce
    from app.backtesting.universal_order_registry import UniversalOrderRegistry
    registry = UniversalOrderRegistry()
    registry.submit(SimOrder("ioc", "AAA", ExecutionSide.BUY, 5, OrderType.MARKET, submitted_at_ns=1, time_in_force=TimeInForce.IOC))
    engine = UniversalEventBacktestEngine(100_000.0, order_registry=registry)
    event = HistoricalRecord("test", "AAA", "tick", 1, {"price": 100.0, "book": OrderBook(asks=(DepthLevel(101.0, 2),))}, 1)
    result = engine.run([event], lambda ctx: EventSignal("HOLD"), order_book_field="book")
    state = registry.lifecycle("ioc").state
    assert state.status.value == "CANCELLED"
    assert state.filled_quantity == 2
    assert state.remaining_quantity == 3
    assert registry.open_orders() == ()
    assert engine.portfolio.positions["AAA"].quantity == 2
    assert result.fill_count == 1


def test_universal_engine_fok_rejection_removes_order_without_accounting() -> None:
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder, TimeInForce
    from app.backtesting.universal_order_registry import UniversalOrderRegistry
    registry = UniversalOrderRegistry()
    registry.submit(SimOrder("fok", "AAA", ExecutionSide.BUY, 5, OrderType.MARKET, submitted_at_ns=1, time_in_force=TimeInForce.FOK))
    engine = UniversalEventBacktestEngine(100_000.0, order_registry=registry)
    event = HistoricalRecord("test", "AAA", "tick", 1, {"price": 100.0, "book": OrderBook(asks=(DepthLevel(101.0, 2),))}, 1)
    result = engine.run([event], lambda ctx: EventSignal("HOLD"), order_book_field="book")
    state = registry.lifecycle("fok").state
    assert state.status.value == "REJECTED"
    assert state.filled_quantity == 0
    assert state.remaining_quantity == 5
    assert registry.open_orders() == ()
    assert engine.portfolio.snapshot().positions == ()
    assert result.fill_count == 0


def test_universal_engine_keeps_partial_order_resting_and_exposes_updated_open_order() -> None:
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder
    from app.backtesting.universal_order_registry import UniversalOrderRegistry

    registry = UniversalOrderRegistry()
    registry.submit(SimOrder("resting", "AAA", ExecutionSide.BUY, 3, OrderType.MARKET, submitted_at_ns=1))
    seen = []

    def strategy(ctx):
        seen.append(tuple((order.order_id, order.quantity) for order in ctx.open_orders))
        return EventSignal("HOLD")

    events = [
        HistoricalRecord("test", "AAA", "tick", 1, {"price": 100.0, "book": OrderBook(asks=(DepthLevel(101.0, 1),))}, 1),
        HistoricalRecord("test", "AAA", "tick", 2, {"price": 102.0, "book": OrderBook(asks=(DepthLevel(102.0, 2),))}, 2),
    ]

    engine = UniversalEventBacktestEngine(100_000.0, order_registry=registry)
    result = engine.run(events, strategy, order_book_field="book")

    assert seen[0] == (("resting", 2),)
    assert seen[1] == ()
    assert registry.lifecycle("resting").state.status.value == "FILLED"
    assert registry.open_orders() == ()
    assert result.fill_count == 2
    assert engine.portfolio.positions["AAA"].quantity == 3


def test_universal_engine_uses_deterministic_event_order_and_clock():
    from app.backtesting.clock import BacktestClock

    clock = BacktestClock()
    seen = []

    def strategy(ctx):
        seen.append((ctx.source, ctx.instrument, clock.now_ns))
        return EventSignal("HOLD")

    events = [
        _event(10, "BBB", 200.0, 1),
        _event(10, "AAA", 100.0, 1),
    ]
    result = UniversalEventBacktestEngine(100_000.0, clock=clock).run(events, strategy)

    assert seen == [("test", "AAA", 10), ("test", "BBB", 10)]
    assert clock.now_ns == 10
    assert result.fill_count == 0

