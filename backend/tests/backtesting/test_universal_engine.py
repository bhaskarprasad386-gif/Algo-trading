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
        raise AssertionError("expected invalid timestamp rejection")


def test_universal_engine_multi_leg_rejects_unhashable_sequence_as_invalid_input():
    malformed = HistoricalRecord("test", "AAA", "tick", 10, {"price": 100.0}, [])
    engine = UniversalEventBacktestEngine(100_000.0)

    try:
        engine.run_multi_leg([malformed], lambda ctx: ())
    except ValueError as exc:
        assert "sequence" in str(exc)
    else:
        raise AssertionError("expected invalid sequence rejection")


def test_universal_engine_multi_leg_rejects_duplicate_event_identity():
    events = [_event(1, "AAA", 100.0, 1), _event(1, "AAA", 100.0, 1)]
    engine = UniversalEventBacktestEngine(100_000.0)

    try:
        engine.run_multi_leg(events, lambda ctx: ())
    except ValueError as exc:
        assert "duplicate event identity" in str(exc)
    else:
        raise AssertionError("expected duplicate event identity rejection")



def test_universal_engine_durable_mode_streams_results_without_retaining_full_history():
    class Writer:
        def __init__(self):
            self.events = []
            self.equity = []

        def record_event(self, sequence, timestamp_ns, event_type, payload):
            self.events.append((sequence, timestamp_ns, event_type, payload))

        def record_equity(self, point):
            self.equity.append(point)

        def complete(self):
            pass

        def fail(self, reason):
            pass

    writer = Writer()
    events = [
        _event(1, "AAA", 100.0, 1),
        _event(2, "AAA", 101.0, 2),
        _event(3, "AAA", 102.0, 3),
    ]

    engine = UniversalEventBacktestEngine(
        100_000.0,
        result_writer=writer,
        retain_history=False,
    )
    result = engine.run(events, lambda context: EventSignal("HOLD"))

    assert result.fill_count == 0
    assert result.final_equity == 100_000.0
    assert len(writer.events) == len(events)
    assert len(writer.equity) == len(events)
    assert result.snapshots == ()
    assert result.equity_curve == ()



def test_universal_engine_is_single_use_and_rejects_second_run():
    engine = UniversalEventBacktestEngine(100_000.0)
    first = engine.run([_event(1, "AAA", 100.0, 1)], lambda context: EventSignal("HOLD"))
    assert first.final_equity == 100_000.0

    try:
        engine.run([_event(1, "BBB", 200.0, 1)], lambda context: EventSignal("HOLD"))
    except RuntimeError as exc:
        assert "single-use" in str(exc)
    else:
        raise AssertionError("expected second run to be rejected")

def _context_components():
    from app.backtesting.backtest_run import BacktestRunSpec
    from app.backtesting.backtest_resolution import BacktestResolution
    from app.backtesting.clock import BacktestClock
    from app.backtesting.execution import ExecutionSimulator
    from app.backtesting.portfolio import Portfolio

    spec = BacktestRunSpec(
        run_id="context-engine",
        strategy_id="universal",
        strategy_version="v1",
        instrument="AAA",
        start_ns=1,
        end_ns=2,
        resolution=BacktestResolution("tick", "historical", 1, 2),
        parameters={},
        data_watermarks={"AAA": 2},
    )
    clock = BacktestClock()
    portfolio = Portfolio(100_000.0)
    execution = ExecutionSimulator()
    events = [_event(1, "AAA", 100.0, 1), _event(2, "AAA", 101.0, 2)]

    class Source:
        def iter_events(self, *, start_ns=None, end_ns=None):
            for event in events:
                if (start_ns is None or event.timestamp_ns >= start_ns) and (end_ns is None or event.timestamp_ns <= end_ns):
                    yield event

    return spec, clock, portfolio, execution, Source()


def test_universal_engine_runs_from_explicit_run_context():
    from app.backtesting.contracts import RunContext

    spec, clock, portfolio, execution, source = _context_components()
    seen = []
    strategy = lambda ctx: (seen.append(ctx.timestamp_ns) or EventSignal("HOLD"))
    context = RunContext(spec, clock, source, strategy, execution, portfolio)

    engine = UniversalEventBacktestEngine(
        100_000.0,
        portfolio=portfolio,
        execution=execution,
        clock=clock,
    )
    result = engine.run_context(context)

    assert seen == [1, 2]
    assert result.final_equity == 100_000.0
    assert clock.now_ns == 2


def test_universal_engine_rejects_run_context_with_different_injected_dependency():
    from app.backtesting.contracts import RunContext
    from app.backtesting.clock import BacktestClock

    spec, clock, portfolio, execution, source = _context_components()
    context = RunContext(spec, clock, source, lambda ctx: EventSignal("HOLD"), execution, portfolio)

    other_engine = UniversalEventBacktestEngine(
        100_000.0,
        portfolio=portfolio,
        execution=execution,
        clock=BacktestClock(),
    )

    try:
        other_engine.run_context(context)
    except ValueError as exc:
        assert "dependencies" in str(exc)
    else:
        raise AssertionError("expected context dependency mismatch rejection")


def test_universal_engine_runs_multi_leg_from_explicit_run_context():
    from app.backtesting.contracts import RunContext
    from app.backtesting.backtest_run import BacktestRunSpec
    from app.backtesting.backtest_resolution import BacktestResolution
    from app.backtesting.clock import BacktestClock
    from app.backtesting.execution import DepthLevel, ExecutionSide, ExecutionSimulator, OrderBook, OrderType, SimOrder
    from app.backtesting.portfolio import Portfolio

    spec = BacktestRunSpec(
        run_id="context-multi",
        strategy_id="universal",
        strategy_version="v1",
        instrument="AAA",
        start_ns=10,
        end_ns=10,
        resolution=BacktestResolution("tick", "historical", 10, 10),
        parameters={},
        data_watermarks={"AAA": 10, "BBB": 10},
    )
    clock = BacktestClock()
    portfolio = Portfolio(100_000.0)
    execution = ExecutionSimulator()
    source = type("Source", (), {
        "iter_events": lambda self, *, start_ns=None, end_ns=None: iter([_event(10, "AAA", 100.0, 1)])
    })()
    books = {
        "AAA": OrderBook(asks=(DepthLevel(101.0, 1),)),
        "BBB": OrderBook(bids=(DepthLevel(199.0, 1),)),
    }

    def strategy(context):
        return (
            (SimOrder("leg-a", "AAA", ExecutionSide.BUY, 1, OrderType.MARKET, submitted_at_ns=context.timestamp_ns), books["AAA"], context.timestamp_ns),
            (SimOrder("leg-b", "BBB", ExecutionSide.SELL, 1, OrderType.MARKET, submitted_at_ns=context.timestamp_ns), books["BBB"], context.timestamp_ns),
        )

    context = RunContext(spec, clock, source, strategy, execution, portfolio)
    engine = UniversalEventBacktestEngine(100_000.0, portfolio=portfolio, execution=execution, clock=clock)
    result = engine.run_multi_leg_context(context)

    assert result.fill_count == 2
    assert portfolio.positions["AAA"].quantity == 1
    assert portfolio.positions["BBB"].quantity == -1
    assert clock.now_ns == 10


def test_universal_engine_context_uses_context_result_writer():
    from app.backtesting.backtest_run import BacktestRunSpec
    from app.backtesting.backtest_resolution import BacktestResolution
    from app.backtesting.clock import BacktestClock
    from app.backtesting.contracts import RunContext
    from app.backtesting.execution import ExecutionSimulator
    from app.backtesting.portfolio import Portfolio

    class Writer:
        def __init__(self):
            self.completed = False
            self.failed = False

        def record_event(self, *args):
            pass

        def record_equity(self, *args):
            pass

        def complete(self):
            self.completed = True

        def fail(self, reason):
            self.failed = True

    spec = BacktestRunSpec("context-writer", "universal", "v1", "AAA", 1, 1, BacktestResolution("tick", "historical", 1, 1))
    clock = BacktestClock()
    portfolio = Portfolio(100_000.0)
    execution = ExecutionSimulator()
    writer = Writer()
    source = type("Source", (), {"iter_events": lambda self, *, start_ns=None, end_ns=None: iter([_event(1, "AAA", 100.0, 1)])})()
    context = RunContext(spec, clock, source, lambda ctx: EventSignal("HOLD"), execution, portfolio, writer)

    engine = UniversalEventBacktestEngine(100_000.0, portfolio=portfolio, execution=execution, clock=clock, result_writer=writer, retain_history=False)
    engine.run_context(context)

    assert writer.completed is True
    assert writer.failed is False


def _real_writer(tmp_path, run_id):
    from app.backtesting.backtest_resolution import BacktestResolution
    from app.backtesting.backtest_result import BacktestRunWriter
    from app.backtesting.backtest_run import BacktestRunSpec
    from app.backtesting.result_ledger import BacktestResultLedger

    ledger = BacktestResultLedger(tmp_path / f"{run_id}.db")
    spec = BacktestRunSpec(
        run_id=run_id,
        strategy_id="universal",
        strategy_version="v1",
        instrument="AAA",
        start_ns=1,
        end_ns=10,
        resolution=BacktestResolution("tick", "historical", 1, 10),
        parameters={},
        data_watermarks={"AAA": 10},
    )
    return ledger, BacktestRunWriter(ledger, spec)


def test_universal_engine_completes_real_result_writer_on_success(tmp_path):
    ledger, writer = _real_writer(tmp_path, "universal-success")
    engine = UniversalEventBacktestEngine(100_000.0, result_writer=writer, retain_history=False)

    engine.run([_event(1, "AAA", 100.0, 1)], lambda context: EventSignal("HOLD"))

    assert ledger.run("universal-success")["status"] == "COMPLETED"


def test_universal_engine_fails_real_result_writer_and_reraises_run_error(tmp_path):
    import pytest

    ledger, writer = _real_writer(tmp_path, "universal-failure")
    engine = UniversalEventBacktestEngine(100_000.0, result_writer=writer, retain_history=False)

    def strategy(context):
        raise RuntimeError("strategy exploded")

    with pytest.raises(RuntimeError, match="strategy exploded"):
        engine.run([_event(1, "AAA", 100.0, 1)], strategy)

    assert ledger.run("universal-failure")["status"] == "FAILED"
    assert ledger.events("universal-failure")[-1]["event_type"] == "RUN_FAILED"


def test_universal_engine_completes_real_result_writer_on_multi_leg_success(tmp_path):
    ledger, writer = _real_writer(tmp_path, "universal-multi-success")
    engine = UniversalEventBacktestEngine(100_000.0, result_writer=writer, retain_history=False)

    engine.run_multi_leg([_event(1, "AAA", 100.0, 1)], lambda context: ())

    assert ledger.run("universal-multi-success")["status"] == "COMPLETED"


def test_universal_engine_fails_real_result_writer_and_reraises_multi_leg_error(tmp_path):
    import pytest

    ledger, writer = _real_writer(tmp_path, "universal-multi-failure")
    engine = UniversalEventBacktestEngine(100_000.0, result_writer=writer, retain_history=False)

    def strategy(context):
        raise ValueError("multi-leg strategy exploded")

    with pytest.raises(ValueError, match="multi-leg strategy exploded"):
        engine.run_multi_leg([_event(1, "AAA", 100.0, 1)], strategy)

    assert ledger.run("universal-multi-failure")["status"] == "FAILED"
    assert ledger.events("universal-multi-failure")[-1]["event_type"] == "RUN_FAILED"


def test_universal_engine_persists_successful_atomic_fills(tmp_path):
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    ledger, writer = _real_writer(tmp_path, "universal-fill-success")
    engine = UniversalEventBacktestEngine(100_000.0, result_writer=writer, retain_history=False)

    def strategy(context):
        return (
            (SimOrder("cash-order", "CASH", ExecutionSide.BUY, 2, OrderType.MARKET, context.timestamp_ns),
             OrderBook(asks=(DepthLevel(101.0, 2),)), context.timestamp_ns),
            (SimOrder("future-order", "FUT", ExecutionSide.SELL, 2, OrderType.MARKET, context.timestamp_ns),
             OrderBook(bids=(DepthLevel(104.0, 2),)), context.timestamp_ns),
        )

    engine.run_multi_leg([_event(1, "CASH", 100.0, 1)], strategy)

    fills = ledger.fills("universal-fill-success")
    assert len(fills) == 2
    assert {row["order_id"] for row in fills} == {"cash-order", "future-order"}
    assert {row["instrument"] for row in fills} == {"CASH", "FUT"}
    assert {row["quantity"] for row in fills} == {2.0}
    assert {row["price"] for row in fills} == {101.0, 104.0}
    assert {(row["instrument"], row["side"], row["quantity"], row["price"]) for row in fills} == {
        ("CASH", "BUY", 2.0, 101.0),
        ("FUT", "SELL", 2.0, 104.0),
    }
    assert [row["sequence"] for row in fills] == [0, 1]
    assert len(engine.portfolio.trades) == len(fills)


def test_universal_engine_does_not_persist_rejected_atomic_fills(tmp_path):
    import pytest
    from app.backtesting.execution import DepthLevel, ExecutionConfig, ExecutionSide, OrderBook, OrderType, SimOrder

    ledger, writer = _real_writer(tmp_path, "universal-fill-rejected")
    engine = UniversalEventBacktestEngine(
        100_000.0,
        execution_config=ExecutionConfig(allow_partial_fills=False),
        result_writer=writer,
        retain_history=False,
    )

    def strategy(context):
        return (
            (SimOrder("cash-order", "CASH", ExecutionSide.BUY, 2, OrderType.MARKET, context.timestamp_ns),
             OrderBook(asks=(DepthLevel(101.0, 1),)), context.timestamp_ns),
            (SimOrder("future-order", "FUT", ExecutionSide.SELL, 2, OrderType.MARKET, context.timestamp_ns),
             OrderBook(bids=(DepthLevel(104.0, 1),)), context.timestamp_ns),
        )

    with pytest.raises(ValueError, match="insufficient displayed depth"):
        engine.run_multi_leg([_event(1, "CASH", 100.0, 1)], strategy)

    assert ledger.fills("universal-fill-rejected") == []


def test_universal_engine_persists_multiple_depth_fills_with_monotonic_sequences(tmp_path):
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    ledger, writer = _real_writer(tmp_path, "universal-multi-fill")
    engine = UniversalEventBacktestEngine(100_000.0, result_writer=writer, retain_history=False)

    def strategy(context):
        return (
            (
                SimOrder("depth-order", "AAA", ExecutionSide.BUY, 5, OrderType.MARKET, context.timestamp_ns),
                OrderBook(
                    asks=(DepthLevel(101.0, 2), DepthLevel(102.0, 3)),
                ),
                context.timestamp_ns,
            ),
        )

    result = engine.run_multi_leg([_event(1, "AAA", 100.0, 1)], strategy)

    assert result.fill_count == 2
    assert len(result.snapshots[-1].positions) == 1
    assert result.snapshots[-1].positions[0].quantity == 5
    fills = ledger.fills("universal-multi-fill", limit=10)
    assert len(fills) == 2
    assert [row["sequence"] for row in fills] == [0, 1]
    assert [row["quantity"] for row in fills] == [2.0, 3.0]
    assert [row["price"] for row in fills] == [101.0, 102.0]
    assert [row["metadata_json"] for row in fills] == ['{"reference_price":101.0}', '{"reference_price":102.0}']
    assert fills[0]["fill_id"] != fills[1]["fill_id"]

    page = ledger.fills("universal-multi-fill", limit=1, after_sequence=0)
    assert len(page) == 1
    assert page[0]["sequence"] == 1
    assert page[0]["quantity"] == 3.0



def test_universal_engine_does_not_call_trade_reporter_when_accounting_fails() -> None:
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder
    from app.backtesting.portfolio import Portfolio

    class Reporter:
        def __init__(self):
            self.reports = []

        def record_atomic_trade(self, report):
            self.reports.append(report)

    class FailingPortfolio(Portfolio):
        def apply_fills_atomic(self, fills, marks=None):
            raise RuntimeError("accounting failed")

    reporter = Reporter()
    portfolio = FailingPortfolio(100_000.0)
    engine = UniversalEventBacktestEngine(100_000.0, portfolio=portfolio, trade_reporter=reporter)

    def strategy(ctx):
        return (
            (SimOrder("leg-a", "AAA", ExecutionSide.BUY, 1, OrderType.MARKET, submitted_at_ns=ctx.timestamp_ns),
             OrderBook(asks=(DepthLevel(101.0, 1),)), ctx.timestamp_ns),
        )

    try:
        engine.run_multi_leg([_event(10, "AAA", 100.0, 1)], strategy)
    except RuntimeError as exc:
        assert str(exc) == "accounting failed"
    else:
        raise AssertionError("expected accounting failure")

    assert reporter.reports == []


def test_universal_engine_reports_atomic_execution_with_post_accounting_trades() -> None:
    from app.backtesting.contracts import AtomicTradeReportInput
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    class Reporter:
        def __init__(self):
            self.reports = []

        def record_atomic_trade(self, report):
            self.reports.append(report)

    reporter = Reporter()
    books = {
        "OLD": OrderBook(bids=(DepthLevel(99.0, 10),), asks=(DepthLevel(101.0, 10),)),
        "NEW": OrderBook(bids=(DepthLevel(199.0, 10),), asks=(DepthLevel(201.0, 10),)),
    }

    def strategy(ctx):
        return (
            (SimOrder("leg-a", "OLD", ExecutionSide.BUY, 2, OrderType.MARKET, submitted_at_ns=ctx.timestamp_ns),
             books["OLD"], ctx.timestamp_ns),
            (SimOrder("leg-b", "NEW", ExecutionSide.SELL, 2, OrderType.MARKET, submitted_at_ns=ctx.timestamp_ns),
             books["NEW"], ctx.timestamp_ns),
        )

    engine = UniversalEventBacktestEngine(100_000.0, trade_reporter=reporter)
    engine.run_multi_leg([_event(10, "OLD", 100.0, 1)], strategy)

    assert len(reporter.reports) == 1
    report = reporter.reports[0]
    assert isinstance(report, AtomicTradeReportInput)
    assert report.execution.fills[0].instrument == "OLD"
    assert report.execution.fills[1].instrument == "NEW"
    assert report.execution.leg_results[0].reference_prices == (101.0,)
    assert report.execution.leg_results[1].reference_prices == (199.0,)
    assert tuple(trade.instrument for trade in report.accounting_trades) == ("OLD", "NEW")
    assert tuple(trade.timestamp_ns for trade in report.accounting_trades) == (10, 10)
    assert report.accounting_trades[0].price == 101.0
    assert report.accounting_trades[1].price == 199.0


def test_universal_multi_leg_checkpoint_transaction_rolls_back_result_writes(tmp_path):
    import pytest
    from app.backtesting.checkpoint import CheckpointStore
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    ledger, writer = _real_writer(tmp_path, "universal-multi-checkpoint-rollback")

    class FailingCheckpointStore(CheckpointStore):
        def save(self, checkpoint, *, commit=True):
            super().save(checkpoint, commit=False)
            raise RuntimeError("checkpoint persistence failed")

    checkpoint_store = FailingCheckpointStore(ledger.connection)
    engine = UniversalEventBacktestEngine(
        100_000.0,
        result_writer=writer,
        checkpoint_store=checkpoint_store,
        checkpoint_every_events=1,
        retain_history=False,
    )

    def strategy(context):
        return (
            (SimOrder("cash-order", "CASH", ExecutionSide.BUY, 1, OrderType.MARKET, context.timestamp_ns),
             OrderBook(asks=(DepthLevel(101.0, 1),)), context.timestamp_ns),
            (SimOrder("future-order", "FUT", ExecutionSide.SELL, 1, OrderType.MARKET, context.timestamp_ns),
             OrderBook(bids=(DepthLevel(104.0, 1),)), context.timestamp_ns),
        )

    with pytest.raises(RuntimeError, match="checkpoint persistence failed"):
        engine.run_multi_leg([_event(1, "CASH", 100.0, 1)], strategy)

    assert ledger.fills("universal-multi-checkpoint-rollback") == []
    assert ledger.equity("universal-multi-checkpoint-rollback") == []
    assert ledger.checkpoints.load("universal-multi-checkpoint-rollback") is None
    assert [row["event_type"] for row in ledger.events("universal-multi-checkpoint-rollback")] == ["RUN_FAILED"]


def test_universal_multi_leg_checkpoint_resume_matches_fresh_run(tmp_path):
    from app.backtesting.backtest_result import BacktestRunWriter
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    events = [
        _event(1, "CASH", 100.0, 0),
        _event(2, "CASH", 101.0, 1),
        _event(3, "CASH", 102.0, 2),
    ]

    books = {
        "CASH": OrderBook(bids=(DepthLevel(99.0, 10),), asks=(DepthLevel(100.0, 10),)),
        "FUT": OrderBook(bids=(DepthLevel(104.0, 10),), asks=(DepthLevel(105.0, 10),)),
    }

    class StatefulBasket:
        def __init__(self):
            self.done = False

        def __call__(self, context):
            if self.done:
                return ()
            self.done = True
            return (
                (SimOrder("cash-order", "CASH", ExecutionSide.BUY, 1, OrderType.MARKET, context.timestamp_ns), books["CASH"], context.timestamp_ns),
                (SimOrder("future-order", "FUT", ExecutionSide.SELL, 1, OrderType.MARKET, context.timestamp_ns), books["FUT"], context.timestamp_ns),
            )

        def get_state(self):
            return {"done": self.done}

        def set_state(self, state):
            self.done = bool(state["done"])

    fresh = UniversalEventBacktestEngine(100_000.0, retain_history=False).run_multi_leg(events, StatefulBasket())

    ledger, writer = _real_writer(tmp_path, "multi-checkpoint-resume")
    partial = UniversalEventBacktestEngine(
        100_000.0,
        result_writer=writer,
        checkpoint_every_events=2,
        retain_history=False,
    )
    partial.run_multi_leg(events[:2], StatefulBasket())
    ledger.set_status("multi-checkpoint-resume", "FAILED")

    resumed_writer = BacktestRunWriter(ledger, writer.spec, resume=True)
    resumed = UniversalEventBacktestEngine(
        100_000.0,
        result_writer=resumed_writer,
        resume=True,
        retain_history=False,
    )
    result = resumed.run_multi_leg(events, StatefulBasket())

    assert result.final_equity == fresh.final_equity
    assert result.realized_pnl == fresh.realized_pnl
    assert result.unrealized_pnl == fresh.unrealized_pnl
    assert result.net_pnl == fresh.net_pnl
    assert result.fill_count == fresh.fill_count

def test_checkpoint_state_builder_captures_resume_state() -> None:
    class StatefulStrategy:
        def get_state(self):
            return {"counter": 7}

    engine = UniversalEventBacktestEngine(100_000.0)
    record = _event(1_000, "AAA", 101.0, 0)
    accumulator = StreamingStatisticsAccumulator(100_000.0)
    snapshot = engine.portfolio.snapshot({"AAA": 101.0})
    accumulator.update(EquityPoint(record.timestamp_ns, snapshot.equity, snapshot.realized_pnl, snapshot.unrealized_pnl))
    identity = event_identity(record)

    state = engine._build_checkpoint_state(
        processed_events=1,
        replay_sequence=1,
        previous_identity=identity,
        last_marks={"AAA": 101.0},
        accumulator=accumulator,
        peak_equity=snapshot.equity,
        strategy=StatefulStrategy(),
    )

    assert state["source_cursor"] == 1
    assert state["source_event_identity"]["instrument"] == "AAA"
    assert state["portfolio_state"]["cash"] == 100_000.0
    assert state["strategy_state"] == {"counter": 7}
    assert state["statistics_state"]["count"] == 1
    assert state["last_marks"] == {"AAA": 101.0}
    assert state["replay_sequence"] == 1
    assert state["fill_sequence"] == 0
    assert state["clock_now_ns"] == 0


def test_cash_future_reporter_survives_real_checkpoint_resume(tmp_path):
    from app.backtesting.arbitrage_strategy_adapters import (
        CashFutureTradeReporter,
        CashFutureUniversalMultiLegAdapter,
    )
    from app.backtesting.backtest_result import BacktestRunWriter
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    def cf_event(ts, seq, cash_bid, cash_ask, future_bid, future_ask):
        return HistoricalRecord(
            "test",
            "NSE:ABC",
            "tick",
            ts,
            {
                "cash": {"bid": cash_bid, "ask": cash_ask, "bid_quantity": 20, "ask_quantity": 20},
                "future": {"bid": future_bid, "ask": future_ask, "bid_quantity": 20, "ask_quantity": 20},
                "__replay_legs__": {
                    "cash": {"source": "test", "instrument": "NSE:ABC", "timeframe": "tick"},
                    "future": {"source": "test", "instrument": "NFO:ABC-20261231", "timeframe": "tick"},
                },
            },
            seq,
        )

    events = [
        cf_event(1, 0, 100.0, 101.0, 104.0, 105.0),
        cf_event(2, 1, 106.0, 107.0, 102.0, 103.0),
    ]

    def run(events, writer=None, resume=False):
        adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
        reporter = CashFutureTradeReporter(writer) if writer is not None else None
        engine = UniversalEventBacktestEngine(
            100_000.0,
            result_writer=writer,
            trade_reporter=reporter,
            checkpoint_store=writer.checkpoints if writer is not None else None,
            checkpoint_every_events=1 if writer is not None else None,
            resume=resume,
            retain_history=False,
        )

        def strategy(context):
            return adapter(context)

        result = engine.run_multi_leg(events, strategy)
        return result, adapter, reporter

    fresh, _, _ = run(events)

    ledger, writer = _real_writer(tmp_path, "cf-reporter-resume")
    partial, partial_adapter, partial_reporter = run(events[:1], writer)
    assert partial_reporter is not None
    assert partial_reporter.get_state()["open"]["NSE:ABC"]["entry_order_id"].endswith(":OPEN:CASH")
    assert partial_adapter.get_state()["open"] is True
    ledger.set_status("cf-reporter-resume", "FAILED")

    resumed_writer = BacktestRunWriter(ledger, writer.spec, resume=True)
    resumed, resumed_adapter, resumed_reporter = run(events, resumed_writer, resume=True)

    assert resumed.final_equity == fresh.final_equity
    assert resumed.realized_pnl == fresh.realized_pnl
    assert resumed.net_pnl == fresh.net_pnl
    assert resumed.fill_count == fresh.fill_count
    assert resumed_reporter is not None
    assert resumed_reporter.get_state()["open"] == {}
    assert resumed_reporter.get_state()["sequence"] == 2
    assert resumed_adapter.get_state()["open"] is True
    trades = ledger.trades("cf-reporter-resume", limit=10)
    assert len(trades) == 2
    assert {row["leg"] for row in trades} == {"CASH", "FUTURE"}
    assert len({row["metadata_json"] for row in trades}) == 2


def test_checkpoint_state_builder_captures_reporter_state() -> None:
    class StatefulStrategy:
        def get_state(self):
            return {}

    class StatefulReporter:
        def get_state(self):
            return {"open": {"CASH": {"entry_order_id": "CF:1:src:ABC:0:OPEN:CASH"}}, "sequence": 4}

    engine = UniversalEventBacktestEngine(100_000.0, trade_reporter=StatefulReporter())
    record = _event(1_000, "AAA", 101.0, 0)
    accumulator = StreamingStatisticsAccumulator(100_000.0)
    snapshot = engine.portfolio.snapshot({"AAA": 101.0})
    accumulator.update(EquityPoint(record.timestamp_ns, snapshot.equity, snapshot.realized_pnl, snapshot.unrealized_pnl))

    state = engine._build_checkpoint_state(
        processed_events=1,
        replay_sequence=1,
        previous_identity=event_identity(record),
        last_marks={"AAA": 101.0},
        accumulator=accumulator,
        peak_equity=snapshot.equity,
        strategy=StatefulStrategy(),
    )

    assert state["reporter_state"] == {
        "open": {"CASH": {"entry_order_id": "CF:1:src:ABC:0:OPEN:CASH"}},
        "sequence": 4,
    }


def test_universal_single_leg_checkpoint_resume_restores_state(tmp_path):
    events = [_event(1000, "AAA", 100.0, 0), _event(2000, "AAA", 101.0, 1), _event(3000, "AAA", 102.0, 2)]

    class StatefulBuy:
        def __init__(self):
            self.done = False
        def __call__(self, context):
            if self.done:
                return "HOLD"
            self.done = True
            return "BUY"
        def get_state(self):
            return {"done": self.done}
        def set_state(self, state):
            self.done = bool(state["done"])

    fresh = UniversalEventBacktestEngine(100_000.0).run(events, StatefulBuy())
    ledger, writer = _real_writer(tmp_path, "checkpoint-resume")
    partial = UniversalEventBacktestEngine(100_000.0, result_writer=writer, checkpoint_every_events=2, retain_history=False)
    partial.run(events[:2], StatefulBuy())
    ledger.set_status("checkpoint-resume", "FAILED")

    resumed_writer = BacktestRunWriter(ledger, writer.spec, resume=True)
    resumed = UniversalEventBacktestEngine(100_000.0, result_writer=resumed_writer, resume=True, retain_history=False)
    resumed_result = resumed.run(events, StatefulBuy())

    assert resumed_result.final_equity == fresh.final_equity
    assert resumed_result.realized_pnl == fresh.realized_pnl
    assert resumed_result.unrealized_pnl == fresh.unrealized_pnl
    assert resumed_result.net_pnl == fresh.net_pnl
