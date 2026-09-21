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
    assert fills[0]["fill_id"] != fills[1]["fill_id"]

    page = ledger.fills("universal-multi-fill", limit=1, after_sequence=0)
    assert len(page) == 1
    assert page[0]["sequence"] == 1
    assert page[0]["quantity"] == 3.0
