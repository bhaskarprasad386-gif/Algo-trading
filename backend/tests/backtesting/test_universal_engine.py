from app.backtesting.engine import EventSignal
from app.backtesting.historical_catalog import HistoricalRecord
import pytest

from app.backtesting.universal_engine import UniversalEventBacktestEngine
from app.backtesting.statistics import StreamingStatisticsAccumulator


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



def test_universal_engine_applies_queue_evidence_before_depth_execution() -> None:
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder
    from app.backtesting.universal_order_registry import UniversalOrderRegistry

    registry = UniversalOrderRegistry()
    registry.submit(
        SimOrder(
            "queued",
            "AAA",
            ExecutionSide.BUY,
            1,
            OrderType.LIMIT,
            limit_price=100.0,
            queue_ahead_quantity=5,
            submitted_at_ns=1,
        )
    )
    events = [
        HistoricalRecord(
            "test", "AAA", "tick", 1,
            {
                "price": 100.0,
                "book": OrderBook(asks=(DepthLevel(100.0, 1),)),
                "queue_evidence": [{"price": 100.0, "executed_quantity": 2}],
            },
            1,
        ),
        HistoricalRecord(
            "test", "AAA", "tick", 2,
            {
                "price": 100.0,
                "book": OrderBook(asks=(DepthLevel(100.0, 1),)),
                "queue_evidence": [{"price": 100.0, "executed_quantity": 3}],
            },
            2,
        ),
    ]

    engine = UniversalEventBacktestEngine(100_000.0, order_registry=registry)
    result = engine.run(events, lambda ctx: EventSignal("HOLD"), order_book_field="book")

    assert registry.lifecycle("queued").state.status.value == "FILLED"
    assert registry.open_orders() == ()
    assert engine.portfolio.positions["AAA"].quantity == 1
    assert result.fill_count == 1


def test_universal_engine_does_not_advance_queue_without_evidence() -> None:
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder
    from app.backtesting.universal_order_registry import UniversalOrderRegistry

    registry = UniversalOrderRegistry()
    registry.submit(
        SimOrder(
            "queued",
            "AAA",
            ExecutionSide.BUY,
            1,
            OrderType.LIMIT,
            limit_price=100.0,
            queue_ahead_quantity=5,
            submitted_at_ns=1,
        )
    )
    event = HistoricalRecord(
        "test", "AAA", "tick", 1,
        {"price": 100.0, "book": OrderBook(asks=(DepthLevel(100.0, 1),))},
        1,
    )

    result = UniversalEventBacktestEngine(100_000.0, order_registry=registry).run(
        [event], lambda ctx: EventSignal("HOLD"), order_book_field="book"
    )

    assert registry.lifecycle("queued").state.status.value == "ACCEPTED"
    assert registry.open_orders()[0].queue_ahead_quantity == 5
    assert result.fill_count == 0


def test_universal_engine_reserves_and_releases_margin_for_strategy_orders() -> None:
    from app.backtesting.portfolio import RiskConfig

    engine = UniversalEventBacktestEngine(
        1_000.0,
        risk_config=RiskConfig(initial_margin_rate=1.0),
        quantity=5,
    )
    result = engine.run(
        [_event(1, "AAA", 100.0, 1)],
        lambda ctx: EventSignal("BUY"),
    )

    assert result.fill_count == 1
    assert engine.portfolio.reserved_margin == 0.0
    assert engine.portfolio.positions["AAA"].quantity == 5


def test_universal_engine_blocks_strategy_order_when_margin_is_insufficient() -> None:
    from app.backtesting.portfolio import RiskConfig

    engine = UniversalEventBacktestEngine(
        100.0,
        risk_config=RiskConfig(initial_margin_rate=1.0),
        quantity=2,
    )
    result = engine.run(
        [_event(1, "AAA", 100.0, 1)],
        lambda ctx: EventSignal("BUY"),
    )

    assert result.fill_count == 0
    assert engine.portfolio.positions == {}
    assert engine.portfolio.reserved_margin == 0.0

def test_universal_multi_leg_rejection_is_durable_before_run_failure() -> None:
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder

    class Writer:
        def __init__(self):
            self.events = []
            self.failed = None
            self.completed = False

        def record_event(self, sequence, timestamp_ns, event_type, payload):
            self.events.append((sequence, timestamp_ns, event_type, payload))
            return 1

        def fail(self, reason):
            self.failed = reason

        def complete(self):
            self.completed = True

    writer = Writer()
    events = [
        HistoricalRecord(
            "test", "AAA", "tick", 1,
            {"price": 100.0},
            1,
        )
    ]

    def strategy(ctx):
        return (
            (
                SimOrder("leg-a", "AAA", ExecutionSide.BUY, 2, OrderType.MARKET, submitted_at_ns=1),
                OrderBook(asks=(DepthLevel(101.0, 1),)),
                1,
            ),
            (
                SimOrder("leg-b", "BBB", ExecutionSide.BUY, 1, OrderType.MARKET, submitted_at_ns=1),
                OrderBook(asks=(DepthLevel(201.0, 1),)),
                1,
            ),
        )

    engine = UniversalEventBacktestEngine(100_000.0, result_writer=writer)
    with pytest.raises(ValueError, match="atomic"):
        engine.run_multi_leg(events, strategy)

    rejection = [event for event in writer.events if event[2] == "ATOMIC_EXECUTION_REJECTED"]
    assert len(rejection) == 1
    assert rejection[0][0] == 0
    assert rejection[0][1] == 1
    assert rejection[0][3]["leg_count"] == 2
    assert rejection[0][3]["filled_quantity"] == 0
    assert "atomic" in rejection[0][3]["reason"]
    assert writer.failed is not None
    assert writer.completed is False

def test_universal_checkpoint_due_uses_composed_transaction(monkeypatch) -> None:
    events = [
        HistoricalRecord("test", "AAA", "tick", 1, {"price": 100.0}, 1),
    ]

    class FailingWriter:
        def __init__(self):
            self.events = []
            self.checkpoint_saves = []

        def transaction(self):
            from contextlib import contextmanager
            @contextmanager
            def tx():
                yield
                raise RuntimeError("abort checkpoint transaction")
            return tx()

        def record_event(self, *args):
            self.events.append(args)

        def record_equity(self, *args):
            pass

        def fail(self, reason):
            pass

        def complete(self):
            pass

        @property
        def checkpoints(self):
            return self

        def save(self, checkpoint, *, commit=True):
            self.checkpoint_saves.append(commit)

    writer = FailingWriter()
    engine = UniversalEventBacktestEngine(
        100_000.0,
        result_writer=writer,
        checkpoint_every_events=1,
    )

    with pytest.raises(RuntimeError, match="abort checkpoint transaction"):
        engine.run(events, lambda ctx: None)

    assert writer.checkpoint_saves == [False]



def test_universal_checkpoint_rejects_risk_config_mismatch() -> None:
    from types import SimpleNamespace
    from app.backtesting.portfolio import RiskConfig

    engine = UniversalEventBacktestEngine(100_000.0, risk_config=RiskConfig(max_leverage=2.0))
    accumulator = StreamingStatisticsAccumulator(100_000.0)
    state = engine._build_checkpoint_state(
        processed_events=1,
        replay_sequence=0,
        previous_identity=SimpleNamespace(timestamp_ns=1, source="test", instrument="NFO:ABC", timeframe="tick", sequence=0),
        last_marks={},
        accumulator=accumulator,
        peak_equity=100_000.0,
        strategy=lambda ctx: EventSignal("HOLD"),
    )
    state["portfolio_state"]["risk_config"]["max_leverage"] = 3.0
    checkpoint = SimpleNamespace(processed_events=1, state=state)
    with pytest.raises(ValueError, match="checkpoint risk_config does not match engine portfolio risk_config"):
        engine._restore_checkpoint_state(
            engine, checkpoint, lambda ctx: EventSignal("HOLD"), accumulator, {}
        )

def test_universal_checkpoint_rejects_initial_capital_mismatch() -> None:
    from types import SimpleNamespace

    engine = UniversalEventBacktestEngine(100_000.0)
    accumulator = StreamingStatisticsAccumulator(100_000.0)
    state = engine._build_checkpoint_state(
        processed_events=1,
        replay_sequence=0,
        previous_identity=SimpleNamespace(timestamp_ns=1, source="test", instrument="NFO:ABC", timeframe="tick", sequence=0),
        last_marks={},
        accumulator=accumulator,
        peak_equity=100_000.0,
        strategy=lambda ctx: EventSignal("HOLD"),
    )
    state["portfolio_state"]["initial_cash"] = 125_000.0
    checkpoint = SimpleNamespace(processed_events=1, state=state)
    with pytest.raises(ValueError, match="checkpoint initial_cash does not match engine portfolio initial_cash"):
        engine._restore_checkpoint_state(
            engine, checkpoint, lambda ctx: EventSignal("HOLD"), accumulator, {}
        )

def test_universal_fill_count_is_restored_from_checkpoint_state() -> None:
    from types import SimpleNamespace
    from app.backtesting.event_model import event_identity
    from app.backtesting.statistics import StreamingStatisticsAccumulator

    engine = UniversalEventBacktestEngine(100_000.0)
    engine._fill_count = 7
    engine._fill_sequence = 7
    accumulator = StreamingStatisticsAccumulator(100_000.0)
    record = _event(1, "AAA", 100.0, 1)
    state = engine._build_checkpoint_state(
        processed_events=1,
        replay_sequence=1,
        previous_identity=event_identity(record),
        last_marks={"AAA": 100.0},
        accumulator=accumulator,
        peak_equity=100_000.0,
        strategy=lambda ctx: EventSignal("HOLD"),
    )
    assert state["fill_count"] == 7

    restored = UniversalEventBacktestEngine(100_000.0)
    restored_accumulator = StreamingStatisticsAccumulator(100_000.0)
    checkpoint = SimpleNamespace(processed_events=1, state=state)
    restored._restore_checkpoint_state(
        restored, checkpoint, lambda ctx: EventSignal("HOLD"), restored_accumulator, {}
    )
    assert restored._fill_count == 7


def test_universal_engine_builds_strategy_context_snapshot_once_per_event(monkeypatch) -> None:
    events = [_event(1, "AAA", 100.0, 1), _event(2, "AAA", 101.0, 2)]
    calls = {"snapshot": 0}
    original = Portfolio.snapshot

    def counted(self, marks):
        calls["snapshot"] += 1
        return original(self, marks)

    monkeypatch.setattr(Portfolio, "snapshot", counted)

    UniversalEventBacktestEngine(100_000.0).run(
        events,
        lambda ctx: EventSignal("HOLD"),
    )

    # One strategy-context snapshot plus one replay-point snapshot per event,
    # rather than two identical context snapshots per event.
    assert calls["snapshot"] == len(events) + 1


def test_universal_durable_engine_rejects_capital_mismatch(tmp_path) -> None:
    from app.backtesting.backtest_resolution import BacktestResolution
    from app.backtesting.backtest_result import BacktestRunWriter
    from app.backtesting.backtest_run import BacktestRunSpec
    from app.backtesting.result_ledger import BacktestResultLedger
    ledger = BacktestResultLedger(tmp_path / "capital-mismatch.db")
    spec = BacktestRunSpec("capital-mismatch", "universal", "v1", "NFO:ABC", 1, 2, BacktestResolution("tick", "historical", 1, 2), initial_capital=125_000.0)
    writer = BacktestRunWriter(ledger, spec)
    with pytest.raises(ValueError, match="match portfolio initial_cash"):
        UniversalEventBacktestEngine(100_000.0, result_writer=writer)



def test_universal_checkpoint_rejects_execution_config_mismatch() -> None:
    from types import SimpleNamespace
    from app.backtesting.execution import ExecutionConfig
    from app.backtesting.statistics import StreamingStatisticsAccumulator

    engine = UniversalEventBacktestEngine(
        100_000.0,
        execution_config=ExecutionConfig(slippage_bps=5.0),
    )
    accumulator = StreamingStatisticsAccumulator(100_000.0)
    state = engine._build_checkpoint_state(
        processed_events=1,
        replay_sequence=1,
        previous_identity=SimpleNamespace(
            timestamp_ns=1, source="test", instrument="NFO:ABC",
            timeframe="tick", sequence=0,
        ),
        last_marks={},
        accumulator=accumulator,
        peak_equity=100_000.0,
        strategy=lambda ctx: EventSignal("HOLD"),
    )
    state["execution_state"]["config"]["slippage_bps"] = 7.0
    checkpoint = SimpleNamespace(processed_events=1, state=state)
    with pytest.raises(ValueError, match="checkpoint execution config does not match"):
        engine._restore_checkpoint_state(
            engine, checkpoint, lambda ctx: EventSignal("HOLD"), accumulator, {}
        )


def test_universal_checkpoint_rejects_execution_model_class_mismatch() -> None:
    from types import SimpleNamespace
    from app.backtesting.execution import ExecutionConfig, ExecutionSimulator
    from app.backtesting.statistics import StreamingStatisticsAccumulator

    class CustomExecution(ExecutionSimulator):
        pass

    engine = UniversalEventBacktestEngine(
        100_000.0,
        execution=ExecutionSimulator(ExecutionConfig()),
    )
    accumulator = StreamingStatisticsAccumulator(100_000.0)
    state = engine._build_checkpoint_state(
        processed_events=1,
        replay_sequence=1,
        previous_identity=SimpleNamespace(
            timestamp_ns=1, source="test", instrument="NFO:ABC",
            timeframe="tick", sequence=0,
        ),
        last_marks={},
        accumulator=accumulator,
        peak_equity=100_000.0,
        strategy=lambda ctx: EventSignal("HOLD"),
    )
    state["execution_state"] = {"kind": "custom", "class": f"{CustomExecution.__module__}.{CustomExecution.__qualname__}"}
    checkpoint = SimpleNamespace(processed_events=1, state=state)
    with pytest.raises(ValueError, match="checkpoint execution model class does not match"):
        engine._restore_checkpoint_state(
            engine, checkpoint, lambda ctx: EventSignal("HOLD"), accumulator, {}
        )

def test_universal_queue_evidence_validation_is_atomic() -> None:
    from app.backtesting.execution import ExecutionSide, OrderType, SimOrder
    from app.backtesting.universal_order_registry import UniversalOrderRegistry

    registry = UniversalOrderRegistry()
    registry.submit(
        SimOrder(
            "queued",
            "AAA",
            ExecutionSide.BUY,
            1,
            OrderType.LIMIT,
            limit_price=100.0,
            queue_ahead_quantity=5,
            submitted_at_ns=1,
        )
    )
    event = HistoricalRecord(
        "test",
        "AAA",
        "tick",
        1,
        {
            "price": 100.0,
            "queue_evidence": [
                {"price": 100.0, "executed_quantity": 2},
                {"price": 100.0, "executed_quantity": "invalid"},
            ],
        },
        1,
    )

    before = registry.export_state()
    engine = UniversalEventBacktestEngine(100_000.0, order_registry=registry)

    with pytest.raises(ValueError, match=r"invalid queue_evidence\[1\]"):
        engine.run([event], lambda ctx: EventSignal("HOLD"))

    assert registry.export_state() == before



def test_universal_checkpoint_resume_matches_uninterrupted_run(tmp_path) -> None:
    from app.backtesting.backtest_resolution import BacktestResolution
    from app.backtesting.backtest_result import BacktestRunWriter
    from app.backtesting.backtest_run import BacktestRunSpec
    from app.backtesting.execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder
    from app.backtesting.portfolio import Portfolio
    from app.backtesting.result_ledger import BacktestResultLedger
    from app.backtesting.universal_order_registry import UniversalOrderRegistry

    def make_events():
        return [
            HistoricalRecord(
                "test", "AAA", "tick", 1,
                {"price": 100.0, "book": OrderBook(asks=(DepthLevel(100.0, 1),))},
                1,
            ),
            HistoricalRecord(
                "test", "AAA", "tick", 2,
                {
                    "price": 101.0,
                    "book": OrderBook(asks=(DepthLevel(101.0, 1),)),
                    "queue_evidence": [{"price": 100.0, "cancelled_quantity_ahead": 5}],
                },
                2,
            ),
            HistoricalRecord(
                "test", "AAA", "tick", 3,
                {"price": 102.0, "book": OrderBook(asks=(DepthLevel(102.0, 2),))},
                3,
            ),
        ]

    def make_state():
        portfolio = Portfolio(100_000.0)
        registry = UniversalOrderRegistry()
        order = SimOrder(
            "queued",
            "AAA",
            ExecutionSide.BUY,
            3,
            OrderType.LIMIT,
            limit_price=100.0,
            queue_ahead_quantity=5,
            submitted_at_ns=1,
        )
        reservation = portfolio.reserve_margin("queued", 30_000.0, {})
        registry.submit(order, reservation)
        return portfolio, registry

    def make_writer(path, run_id):
        ledger = BacktestResultLedger(path)
        spec = BacktestRunSpec(
            run_id, "universal", "v1", "AAA", 1, 3,
            BacktestResolution("tick", "historical", 1, 3),
            initial_capital=100_000.0,
        )
        return ledger, BacktestRunWriter(ledger, spec)

    class StatefulStrategy:
        def __init__(self, count=0):
            self.count = count

        def __call__(self, context):
            self.count += 1
            self._last_timestamp_ns = context.timestamp_ns
            return EventSignal("HOLD")

        def get_state(self):
            return {"count": self.count, "last_timestamp_ns": self._last_timestamp_ns}

        def set_state(self, state):
            self.count = int(state["count"])
            self._last_timestamp_ns = state.get("last_timestamp_ns")

    class StatefulReporter:
        def __init__(self, marker="fresh"):
            self.marker = marker
            self.records_seen = 0

        def record_atomic_trade(self, report):
            self.records_seen += 1

        def get_state(self):
            return {"marker": self.marker, "records_seen": self.records_seen}

        def set_state(self, state):
            self.marker = str(state["marker"])
            self.records_seen = int(state["records_seen"])

    events = make_events()
    ref_portfolio, ref_registry = make_state()
    ref_strategy = StatefulStrategy()
    ref_strategy._last_timestamp_ns = None
    ref_reporter = StatefulReporter(marker="checkpointed")
    ref_reporter.records_seen = 7

    # A: uninterrupted reference run.
    ref_ledger, ref_writer = make_writer(tmp_path / "reference.db", "reference")
    reference = UniversalEventBacktestEngine(
        100_000.0,
        portfolio=ref_portfolio,
        order_registry=ref_registry,
        result_writer=ref_writer,
        trade_reporter=ref_reporter,
        retain_history=False,
    ).run(events, ref_strategy, order_book_field="book")
    ref_writer.complete()

    # B: checkpoint while queue-ahead and reservation are still non-zero.
    resumed_portfolio, resumed_registry = make_state()
    resumed_strategy = StatefulStrategy()
    resumed_strategy._last_timestamp_ns = None
    resumed_reporter = StatefulReporter(marker="checkpointed")
    resumed_reporter.records_seen = 7
    resumed_ledger, first_writer = make_writer(tmp_path / "resumed.db", "resumed")
    first_engine = UniversalEventBacktestEngine(
        100_000.0,
        portfolio=resumed_portfolio,
        order_registry=resumed_registry,
        result_writer=first_writer,
        trade_reporter=resumed_reporter,
        retain_history=False,
        checkpoint_every_events=1,
    )

    def interrupted_events():
        yield events[0]
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        first_engine.run(interrupted_events(), resumed_strategy, order_book_field="book")

    checkpoint = first_writer.checkpoints.load("resumed")
    assert checkpoint is not None
    saved_registry = checkpoint.state["order_registry_state"]
    assert saved_registry["orders"]["queued"]["quantity"] == 3
    assert saved_registry["queue"]["queued"]["queue_ahead_quantity"] == 5
    assert saved_registry["reservations"]["queued"] == pytest.approx(30_000.0)
    assert checkpoint.state["portfolio_state"]["reserved_margin"]["queued"] == pytest.approx(30_000.0)
    assert checkpoint.state["strategy_state"] == {"count": 1, "last_timestamp_ns": 1}
    assert checkpoint.state["reporter_state"] == {"marker": "checkpointed", "records_seen": 7}

    # An interrupted run must be explicitly declared recoverable before resume.
    resumed_ledger.mark_recoverable("resumed")

    # Resume must restore both user-defined strategy state and reporter state.
    resume_strategy = StatefulStrategy()
    resume_strategy._last_timestamp_ns = None
    resume_reporter = StatefulReporter(marker="different")
    resume_writer = BacktestRunWriter(resumed_ledger, first_writer.spec, resume=True)
    resumed = UniversalEventBacktestEngine(
        100_000.0,
        portfolio=resumed_portfolio,
        order_registry=resumed_registry,
        result_writer=resume_writer,
        trade_reporter=resume_reporter,
        retain_history=False,
        checkpoint_every_events=1,
        resume=True,
    ).run(events, resume_strategy, order_book_field="book")
    resume_writer.complete()

    assert resumed.final_equity == pytest.approx(reference.final_equity)
    assert resumed.realized_pnl == pytest.approx(reference.realized_pnl)
    assert resumed.unrealized_pnl == pytest.approx(reference.unrealized_pnl)
    assert resumed.net_pnl == pytest.approx(reference.net_pnl)
    assert resumed.total_return == pytest.approx(reference.total_return)
    assert resumed.sharpe_ratio == pytest.approx(reference.sharpe_ratio)
    assert resumed.sortino_ratio == pytest.approx(reference.sortino_ratio)
    assert resumed.max_drawdown == pytest.approx(reference.max_drawdown)
    assert resumed.cagr == pytest.approx(reference.cagr)
    assert resumed.fill_count == reference.fill_count
    assert resume_strategy.get_state() == ref_strategy.get_state()
    assert resume_reporter.get_state() == ref_reporter.get_state()

    assert resumed_registry.export_state() == ref_registry.export_state()
    assert resumed_portfolio.export_state() == ref_portfolio.export_state()

    ref_fills = ref_ledger.fills("reference", limit=100)
    resumed_fills = resumed_ledger.fills("resumed", limit=100)
    assert [(f.order_id, f.sequence, f.quantity, f.price) for f in resumed_fills] == [
        (f.order_id, f.sequence, f.quantity, f.price) for f in ref_fills
    ]

    ref_equity = ref_ledger.equity("reference", limit=100)
    resumed_equity = resumed_ledger.equity("resumed", limit=100)
    assert [
        (p["timestamp_ns"], p["equity"], p["realized_pnl"], p["unrealized_pnl"], p["drawdown"])
        for p in resumed_equity
    ] == [
        (p["timestamp_ns"], p["equity"], p["realized_pnl"], p["unrealized_pnl"], p["drawdown"])
        for p in ref_equity
    ]
