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
