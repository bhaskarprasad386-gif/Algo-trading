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


def test_universal_engine_rejects_duplicate_event_identity():
    events = [_event(1, "AAA", 100.0, 1), _event(1, "AAA", 100.0, 1)]
    engine = UniversalEventBacktestEngine(100_000.0)

    try:
        engine.run(events, lambda ctx: EventSignal("HOLD"))
    except ValueError as exc:
        assert "duplicate event identity" in str(exc)
    else:
        raise AssertionError("expected duplicate event identity rejection")


def test_universal_engine_runs_from_streaming_data_source():
    class Source:
        def iter_events(self, *, start_ns=None, end_ns=None):
            yield _event(1, "AAA", 100.0, 1)
            yield _event(2, "AAA", 101.0, 1)

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run_source(Source(), lambda ctx: EventSignal("HOLD"))

    assert result.fill_count == 0
    assert result.equity_curve[-1].timestamp_ns == 2


def test_universal_engine_routes_order_to_depth_execution():
    from app.backtesting.execution import DepthLevel, OrderBook

    book = OrderBook(
        bids=(DepthLevel(99.0, 10),),
        asks=(DepthLevel(101.0, 10),),
    )
    record = _event(10, "AAA", 100.0, 1)
    record = HistoricalRecord(record.source, record.instrument, record.timeframe, record.timestamp_ns, {"price": 100.0, "book": book}, record.sequence)

    engine = UniversalEventBacktestEngine(100_000.0, quantity=2)
    result = engine.run(
        [record],
        lambda context: EventSignal("BUY"),
        order_book_field="book",
    )

    assert result.fill_count == 1
    assert engine.portfolio.positions["AAA"].quantity == 2
    assert engine.portfolio.positions["AAA"].average_price == 101.0


def test_universal_engine_executes_multi_leg_basket_atomically():
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    books = {
        "AAA": OrderBook(bids=(DepthLevel(99.0, 10),), asks=(DepthLevel(101.0, 10),)),
        "BBB": OrderBook(bids=(DepthLevel(199.0, 10),), asks=(DepthLevel(201.0, 10),)),
    }

    def strategy(ctx):
        return (
            SimOrder("leg-a", "AAA", ExecutionSide.BUY, 1, OrderType.MARKET, submitted_at_ns=ctx.timestamp_ns),
            SimOrder("leg-b", "BBB", ExecutionSide.SELL, 1, OrderType.MARKET, submitted_at_ns=ctx.timestamp_ns),
        )

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run_multi_leg([_event(10, "AAA", 100.0, 1)], lambda ctx: tuple((order, books[order.instrument], ctx.timestamp_ns) for order in strategy(ctx)))

    assert result.fill_count == 2
    assert engine.portfolio.positions["AAA"].quantity == 1
    assert engine.portfolio.positions["BBB"].quantity == -1


def test_universal_engine_notifies_strategy_after_atomic_rejection():
    from app.backtesting.execution import DepthLevel, ExecutionConfig, ExecutionSide, OrderBook, OrderType, SimOrder

    class Strategy:
        def __init__(self):
            self.callbacks = []

        def __call__(self, context):
            return (
                (SimOrder("leg-a", "AAA", ExecutionSide.BUY, 2, OrderType.MARKET, submitted_at_ns=context.timestamp_ns),
                 OrderBook(asks=(DepthLevel(101.0, 1),)), context.timestamp_ns),
                (SimOrder("leg-b", "BBB", ExecutionSide.SELL, 2, OrderType.MARKET, submitted_at_ns=context.timestamp_ns),
                 OrderBook(bids=(DepthLevel(199.0, 1),)), context.timestamp_ns),
            )

        def on_atomic_execution(self, result):
            self.callbacks.append(result)

    strategy = Strategy()
    engine = UniversalEventBacktestEngine(
        100_000.0,
        execution_config=ExecutionConfig(allow_partial_fills=False),
    )

    try:
        engine.run_multi_leg([_event(10, "AAA", 100.0, 1)], strategy)
    except ValueError as exc:
        assert "insufficient displayed depth" in str(exc)
    else:
        raise AssertionError("expected atomic execution rejection")

    assert len(strategy.callbacks) == 1
    assert strategy.callbacks[0].rejected is True
    assert strategy.callbacks[0].fills == ()
    assert engine.portfolio.trades == ()
    assert engine.portfolio.snapshot().positions == ()


def test_universal_engine_notifies_strategy_only_after_successful_atomic_accounting():
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    class Strategy:
        def __init__(self):
            self.callback_positions = None

        def __call__(self, context):
            return (
                (SimOrder("leg-a", "AAA", ExecutionSide.BUY, 1, OrderType.MARKET, submitted_at_ns=context.timestamp_ns),
                 OrderBook(asks=(DepthLevel(101.0, 1),)), context.timestamp_ns),
                (SimOrder("leg-b", "BBB", ExecutionSide.SELL, 1, OrderType.MARKET, submitted_at_ns=context.timestamp_ns),
                 OrderBook(bids=(DepthLevel(199.0, 1),)), context.timestamp_ns),
            )

        def on_atomic_execution(self, result):
            self.callback_positions = dict(engine.portfolio.positions)

    strategy = Strategy()
    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run_multi_leg([_event(10, "AAA", 100.0, 1)], strategy)

    assert result.fill_count == 2
    assert set(strategy.callback_positions) == {"AAA", "BBB"}
    assert strategy.callback_positions["AAA"].quantity == 1
    assert strategy.callback_positions["BBB"].quantity == -1


def test_universal_engine_does_not_notify_strategy_when_atomic_accounting_fails():
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder
    from app.backtesting.portfolio import Portfolio

    class FailingPortfolio(Portfolio):
        def apply_fills_atomic(self, fills, marks=None):
            raise RuntimeError("accounting failed")

    class Strategy:
        def __init__(self):
            self.callback_count = 0

        def __call__(self, context):
            return (
                (SimOrder("leg-a", "AAA", ExecutionSide.BUY, 1, OrderType.MARKET, submitted_at_ns=context.timestamp_ns),
                 OrderBook(asks=(DepthLevel(101.0, 1),)), context.timestamp_ns),
                (SimOrder("leg-b", "BBB", ExecutionSide.SELL, 1, OrderType.MARKET, submitted_at_ns=context.timestamp_ns),
                 OrderBook(bids=(DepthLevel(199.0, 1),)), context.timestamp_ns),
            )

        def on_atomic_execution(self, result):
            self.callback_count += 1

    strategy = Strategy()
    ledger, writer = _real_writer(tmp_path, "universal-accounting-failure")
    engine = UniversalEventBacktestEngine(
        100_000.0,
        portfolio=FailingPortfolio(100_000.0),
        result_writer=writer,
        retain_history=False,
    )

    import pytest
    with pytest.raises(RuntimeError, match="accounting failed"):
        engine.run_multi_leg([_event(10, "AAA", 100.0, 1)], strategy)

    assert strategy.callback_count == 0
    assert ledger.fills("universal-accounting-failure") == []
    assert ledger.run("universal-accounting-failure")["status"] == "FAILED"


def test_universal_engine_multi_leg_rejects_partial_atomic_execution_without_portfolio_change():
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    books = {
        "AAA": OrderBook(asks=(DepthLevel(101.0, 1),)),
        "BBB": OrderBook(bids=(DepthLevel(199.0, 0),)),
    }

    def strategy(ctx):
        return (
            (SimOrder("leg-a", "AAA", ExecutionSide.BUY, 1, OrderType.MARKET, submitted_at_ns=ctx.timestamp_ns), books["AAA"], ctx.timestamp_ns),
            (SimOrder("leg-b", "BBB", ExecutionSide.SELL, 1, OrderType.MARKET, submitted_at_ns=ctx.timestamp_ns), books["BBB"], ctx.timestamp_ns),
        )

    engine = UniversalEventBacktestEngine(100_000.0)
    try:
        engine.run_multi_leg([_event(10, "AAA", 100.0, 1)], strategy)
    except ValueError as exc:
        assert "atomic rollback" in str(exc)
    else:
        raise AssertionError("expected atomic execution rejection")
    assert engine.portfolio.trades == ()
    assert engine.portfolio.snapshot().positions == ()


def test_universal_engine_multi_leg_requires_atomic_execution_model():
    from app.backtesting.execution import ExecutionSimulator, ExecutionSide, OrderBook, SimOrder

    class NonAtomicExecution(ExecutionSimulator):
        execute_many_atomic = None

    engine = UniversalEventBacktestEngine(100_000.0, execution=NonAtomicExecution())
    with __import__("pytest").raises(TypeError, match="atomic execution model"):
        engine.run_multi_leg([_event(10, "AAA", 100.0, 1)], lambda ctx: ())


def test_universal_engine_persists_single_leg_depth_fills(tmp_path):
    from app.backtesting.execution import DepthLevel, OrderBook

    ledger, writer = _real_writer(tmp_path, "universal-single-depth-fill")
    book = OrderBook(asks=(DepthLevel(101.0, 2), DepthLevel(102.0, 3)))
    record = HistoricalRecord(
        "test", "AAA", "tick", 10,
        {"price": 100.0, "book": book}, 1,
    )
    engine = UniversalEventBacktestEngine(
        100_000.0,
        quantity=5,
        result_writer=writer,
        retain_history=False,
    )

    result = engine.run(
        [record],
        lambda context: EventSignal("BUY"),
        order_book_field="book",
    )

    fills = ledger.fills("universal-single-depth-fill")
    assert result.fill_count == 2
    assert len(fills) == 2
    assert len(engine.portfolio.trades) == len(fills)
    assert [row["sequence"] for row in fills] == [0, 1]
    assert [(row["quantity"], row["price"]) for row in fills] == [(2.0, 101.0), (3.0, 102.0)]
    assert all(row["instrument"] == "AAA" and row["side"] == "BUY" for row in fills)


def test_universal_engine_persists_single_leg_non_depth_fill(tmp_path):
    ledger, writer = _real_writer(tmp_path, "universal-single-fill")
    engine = UniversalEventBacktestEngine(
        100_000.0,
        result_writer=writer,
        retain_history=False,
    )

    engine.run(
        [_event(1, "AAA", 100.0, 1)],
        lambda context: EventSignal("BUY"),
    )

    fills = ledger.fills("universal-single-fill")
    assert len(fills) == 1
    assert engine.portfolio.trades[0].order_id == fills[0]["order_id"]
    assert engine.portfolio.trades[0].instrument == fills[0]["instrument"] == "AAA"
    assert engine.portfolio.trades[0].quantity == fills[0]["quantity"] == 1.0
    assert engine.portfolio.trades[0].price == fills[0]["price"] == 100.0
    assert fills[0]["sequence"] == 0


def test_universal_engine_depth_partial_fill_updates_only_filled_quantity():
    from app.backtesting.execution import DepthLevel, OrderBook

    book = OrderBook(asks=(DepthLevel(101.0, 2),))
    record = HistoricalRecord(
        "test", "AAA", "tick", 10,
        {"price": 100.0, "book": book}, 1,
    )

    engine = UniversalEventBacktestEngine(100_000.0, quantity=5)
    result = engine.run(
        [record],
        lambda context: EventSignal("BUY"),
        order_book_field="book",
    )

    assert result.fill_count == 1
    assert engine.portfolio.positions["AAA"].quantity == 2
    assert engine.portfolio.positions["AAA"].average_price == 101.0
    assert engine.portfolio.trades[0].quantity == 2


def test_universal_engine_depth_non_partial_rejection_leaves_portfolio_unchanged():
    from app.backtesting.execution import DepthLevel, ExecutionConfig, OrderBook

    book = OrderBook(asks=(DepthLevel(101.0, 2),))
    record = HistoricalRecord(
        "test", "AAA", "tick", 10,
        {"price": 100.0, "book": book}, 1,
    )

    engine = UniversalEventBacktestEngine(
        100_000.0,
        quantity=5,
        execution_config=ExecutionConfig(allow_partial_fills=False),
    )

    try:
        engine.run(
            [record],
            lambda context: EventSignal("BUY"),
            order_book_field="book",
        )
    except ValueError as exc:
        assert "insufficient displayed depth" in str(exc)
    else:
        raise AssertionError("expected depth rejection")

    assert engine.portfolio.trades == ()
    assert engine.portfolio.snapshot().positions == ()
    assert engine.portfolio.cash == 100_000.0


def test_universal_engine_multi_leg_validates_event_fields_before_ordering():
    first = _event(10, "AAA", 100.0, 1)
    malformed = HistoricalRecord("test", "AAA", "tick", "bad", {"price": 100.0}, 2)

    engine = UniversalEventBacktestEngine(100_000.0)
    try:
        engine.run_multi_leg([first, malformed], lambda ctx: ())
    except ValueError as exc:
        assert "timestamp_ns" in str(exc)
    else: