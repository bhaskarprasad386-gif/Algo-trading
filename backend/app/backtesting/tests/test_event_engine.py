from dataclasses import dataclass

import pytest

from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventReplayConfig, EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, ExecutionSide, SimOrder, TimeInForce
from app.backtesting.order_lifecycle import OrderStatus
from app.backtesting.portfolio import Portfolio, RiskConfig
from app.backtesting.strategy import StrategyDecision


def test_replays_millisecond_events_without_fabrication():
    events = [MarketEvent(100, "NIFTY", EventType.QUOTE, {"bid": 1}), MarketEvent(101, "NIFTY", EventType.TRADE, {"price": 2})]
    received = []
    result = EventBacktestEngine(EventReplayConfig(timestamp_unit="ms")).run(events, lambda event, state: received.append(event.timestamp_ns))
    assert received == [100_000_000, 101_000_000]
    assert result.events_seen == 2 and result.events_dispatched == 2


def test_supports_microsecond_source_timestamps():
    events = [MarketEvent(1_000_000, "OPT", EventType.QUOTE, {}, sequence=1), MarketEvent(1_000_001, "OPT", EventType.TRADE, {}, sequence=2)]
    received = []
    result = EventBacktestEngine(EventReplayConfig(timestamp_unit="us")).run(events, lambda event, state: received.append(event.timestamp_ns))
    assert received == [1_000_000_000, 1_000_001_000]
    assert result.first_timestamp_ns == 1_000_000_000 and result.last_timestamp_ns == 1_000_001_000


def test_preserves_multiple_events_at_same_timestamp_by_sequence():
    events = [MarketEvent(10, "FUT", EventType.QUOTE, {}, sequence=2), MarketEvent(10, "FUT", EventType.TRADE, {}, sequence=3)]
    received = []
    EventBacktestEngine().run(events, lambda event, state: received.append(event.sequence))
    assert received == [2, 3]


def test_event_strategy_receives_only_prior_history():
    events = [MarketEvent(10, "OPT", EventType.QUOTE, {"bid": 100}, sequence=1), MarketEvent(20, "OPT", EventType.TRADE, {"price": 101}, sequence=2)]
    @dataclass
    class Strategy:
        strategy_id = "history-test"
        strategy_version = "1"
        histories: list[tuple[int, ...]] | None = None
        started: bool = False
        ended: bool = False
        def __post_init__(self): self.histories = []
        def on_start(self, context): self.started = True
        def on_event(self, event, context):
            self.histories.append(tuple(e.timestamp_ns for e in context.history)); return StrategyDecision(action="OBSERVE")
        def on_end(self, context): self.ended = True
    strategy = Strategy()
    result = EventBacktestEngine().run(events, strategy)
    assert strategy.started and strategy.ended and strategy.histories == [(), (10,)]
    assert result.decisions_emitted == 2


def test_event_strategy_supports_custom_event_types_and_decisions():
    event = MarketEvent(100, "NEWS", EventType.CUSTOM, {"headline": "test"})
    seen = []
    class Strategy:
        strategy_id = "custom-event"; strategy_version = "1"
        def on_event(self, current, context):
            seen.append((current.event_type, context.history)); return StrategyDecision(action="BUY", metadata={"reason": "custom"})
    result = EventBacktestEngine().run([event], Strategy())
    assert seen == [(EventType.CUSTOM, ())] and result.decisions_emitted == 1


def test_event_strategy_order_uses_instrument_quote_and_ask_for_buy():
    class Strategy:
        strategy_id = "trade-event"; strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("o1", "NIFTY", ExecutionSide.BUY, 10),))
    portfolio = Portfolio(initial_cash=100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([MarketEvent(1_000, "NIFTY", EventType.QUOTE, {"bid": 99, "ask": 101})], Strategy())
    assert result.fills == 1 and result.final_snapshot is not None
    assert result.final_snapshot.cash == pytest.approx(98_990)
    assert result.final_snapshot.positions[0].quantity == 10
    assert engine.order_states["o1"].status == OrderStatus.FILLED


def test_risk_blocked_order_does_not_change_portfolio():
    class Strategy:
        strategy_id = "risk-block"; strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("blocked", "NIFTY", ExecutionSide.BUY, 20),))
    portfolio = Portfolio(1_000, RiskConfig(initial_margin_rate=1.0))
    result = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio).run(
        [MarketEvent(1, "NIFTY", EventType.QUOTE, {"bid": 99, "ask": 100})], Strategy())
    assert result.fills == 0
    assert result.risk_blocks == 1
    assert result.final_snapshot.cash == 1_000
    assert result.final_snapshot.positions == ()
    assert result.final_snapshot.reserved_margin == 0


def test_multi_leg_risk_failure_is_atomic_in_event_engine():
    class Strategy:
        strategy_id = "atomic-risk"; strategy_version = "1"
        def on_event(self, event, context):
            if event.instrument != "B": return None
            return StrategyDecision(action="SPREAD", orders=(
                SimOrder("a", "A", ExecutionSide.BUY, 5),
                SimOrder("b", "B", ExecutionSide.BUY, 6),
            ))
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.1, max_position_quantity=5))
    events = [
        MarketEvent(1, "A", EventType.QUOTE, {"bid": 99, "ask": 100}),
        MarketEvent(2, "B", EventType.QUOTE, {"bid": 199, "ask": 200}),
    ]
    result = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio).run(events, Strategy())
    assert result.risk_blocks == 1
    assert result.fills == 0
    assert result.final_snapshot.positions == ()
    assert result.final_snapshot.cash == 100_000
    assert result.final_snapshot.reserved_margin == 0


def test_margin_reservation_is_released_after_fill():
    class Strategy:
        strategy_id = "reserve-release"; strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("o1", "NIFTY", ExecutionSide.BUY, 10),))
    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.2, maintenance_margin_rate=0.1))
    result = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio).run(
        [MarketEvent(1, "NIFTY", EventType.QUOTE, {"bid": 99, "ask": 100})], Strategy())
    assert result.fills == 1
    assert result.final_snapshot.reserved_margin == 0
    assert len(portfolio.trades) == 1
    assert portfolio.trades[0].order_id == "o1"


def test_missing_future_leg_quote_is_not_fabricated():
    class Strategy:
        strategy_id = "no-future"; strategy_version = "1"
        def on_event(self, event, context):
            if event.instrument != "FUT_NEAR": return None
            return StrategyDecision(action="SPREAD", orders=(SimOrder("near", "FUT_NEAR", ExecutionSide.BUY, 1), SimOrder("far", "FUT_FAR", ExecutionSide.SELL, 1)))
    portfolio = Portfolio(initial_cash=100_000)
    result = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio).run(
        [MarketEvent(1_000, "FUT_NEAR", EventType.QUOTE, {"bid": 99, "ask": 101})], Strategy())
    assert result.orders_submitted == 2 and result.fills == 1
    assert [p.instrument for p in result.final_snapshot.positions] == ["FUT_NEAR"]


def test_same_timestamp_quotes_can_fill_both_legs():
    class Strategy:
        strategy_id = "same-ts"; strategy_version = "1"
        def on_event(self, event, context):
            if event.instrument != "FUT_FAR": return None
            return StrategyDecision(action="SPREAD", orders=(SimOrder("near", "FUT_NEAR", ExecutionSide.BUY, 1), SimOrder("far", "FUT_FAR", ExecutionSide.SELL, 1)))
    portfolio = Portfolio(initial_cash=100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    events = [MarketEvent(1_000, "FUT_NEAR", EventType.QUOTE, {"bid": 99, "ask": 101}, sequence=1), MarketEvent(1_000, "FUT_FAR", EventType.QUOTE, {"bid": 205, "ask": 207}, sequence=2)]
    result = engine.run(events, Strategy())
    assert result.fills == 2


def test_stale_depth_is_not_used_after_a_newer_quote():
    class Strategy:
        strategy_id = "stale-depth"; strategy_version = "1"
        def on_event(self, event, context):
            if event.event_type != EventType.QUOTE:
                return None
            return StrategyDecision(action="BUY", orders=(SimOrder("o1", "NIFTY", ExecutionSide.BUY, 2),))
    portfolio = Portfolio(initial_cash=100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    events = [
        MarketEvent(1_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 10]], "bids": [[99.0, 10]]}),
        MarketEvent(2_000, "NIFTY", EventType.QUOTE, {"bid": 104.0, "ask": 105.0}),
    ]
    result = engine.run(events, Strategy())
    assert result.fills == 1
    assert result.final_snapshot.cash == pytest.approx(99_790)


def test_latest_depth_snapshot_replaces_previous_depth_without_fabrication():
    class Strategy:
        strategy_id = "dynamic-depth"; strategy_version = "1"
        def on_event(self, event, context):
            if event.sequence != 3:
                return None
            return StrategyDecision(action="BUY", orders=(SimOrder("o1", "NIFTY", ExecutionSide.BUY, 4),))
    portfolio = Portfolio(initial_cash=100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    events = [
        MarketEvent(1_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 10]], "bids": [[99.0, 10]]}, sequence=1),
        MarketEvent(1_500, "NIFTY", EventType.DEPTH, {"asks": [[101.0, 2], [101.5, 5]], "bids": [[100.0, 7]]}, sequence=2),
        MarketEvent(1_500, "NIFTY", EventType.QUOTE, {"bid": 100.0, "ask": 101.0}, sequence=3),
    ]
    result = engine.run(events, Strategy())
    assert result.fills == 2
    assert result.final_snapshot.cash == pytest.approx(100_000 - 2 * 101.0 - 2 * 101.5)


def test_strategy_order_queue_ahead_requires_observed_evidence():
    class Strategy:
        strategy_id = "queue-preserve"; strategy_version = "1"
        def on_event(self, event, context):
            if event.event_type != EventType.DEPTH:
                return None
            return StrategyDecision(action="BUY", orders=(SimOrder("o1", "NIFTY", ExecutionSide.BUY, 3, queue_ahead_quantity=2),))
    portfolio = Portfolio(initial_cash=100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([MarketEvent(1_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 5]], "bids": [[99.0, 5]]})], Strategy())
    assert result.fills == 0
    assert result.final_snapshot.cash == pytest.approx(100_000)
    assert engine.order_states["o1"].status == OrderStatus.ACCEPTED
    assert engine.order_states["o1"].remaining_quantity == 3


def test_ioc_partial_fill_is_cancelled_after_executable_quantity():
    class Strategy:
        strategy_id = "ioc"; strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("ioc-1", "NIFTY", ExecutionSide.BUY, 10, time_in_force=TimeInForce.IOC),))
    portfolio = Portfolio(100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([MarketEvent(1_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 3]], "bids": [[99.0, 5]]})], Strategy())
    assert result.fills == 1
    assert result.final_snapshot.positions[0].quantity == 3
    assert engine.order_states["ioc-1"].status == OrderStatus.CANCELLED
    assert engine.order_states["ioc-1"].remaining_quantity == 7


def test_fok_is_atomic_when_depth_cannot_fill_entire_order():
    class Strategy:
        strategy_id = "fok"; strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("fok-1", "NIFTY", ExecutionSide.BUY, 10, time_in_force=TimeInForce.FOK),))
    portfolio = Portfolio(100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([MarketEvent(1_000, "NIFTY", EventType.DEPTH, {"asks": [[100.0, 3]], "bids": [[99.0, 5]]})], Strategy())
    assert result.fills == 0
    assert result.final_snapshot.positions == ()
    assert engine.order_states["fok-1"].status == OrderStatus.REJECTED


def test_stop_order_waits_for_observed_trigger_before_execution():
    class Strategy:
        strategy_id = "stop"; strategy_version = "1"
        def on_event(self, event, context):
            return StrategyDecision(action="BUY", orders=(SimOrder("stop-1", "NIFTY", ExecutionSide.BUY, 2, order_type=__import__("app.backtesting.execution", fromlist=["OrderType"]).OrderType.STOP, stop_price=101.0),))
    portfolio = Portfolio(100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([
        MarketEvent(1_000, "NIFTY", EventType.TRADE, {"price": 100.0}),
        MarketEvent(2_000, "NIFTY", EventType.TRADE, {"price": 101.0}),
    ], Strategy())
    assert result.fills == 1
    assert engine.order_states["stop-1"].status == OrderStatus.FILLED


def test_strategy_must_return_strategy_decision_or_none():
    class BadStrategy:
        strategy_id = "bad"; strategy_version = "1"
        def on_event(self, event, context): return "BUY"
    with pytest.raises(TypeError, match="StrategyDecision"):
        EventBacktestEngine().run([MarketEvent(1, "X", EventType.TRADE, {})], BadStrategy())


def test_filtered_events_do_not_appear_in_strategy_history():
    events = [MarketEvent(1, "X", EventType.QUOTE, {}), MarketEvent(2, "X", EventType.TRADE, {})]
    histories = []
    class Strategy:
        strategy_id = "filter"; strategy_version = "1"
        def on_event(self, event, context): histories.append(tuple(e.event_type for e in context.history)); return None
    EventBacktestEngine(EventReplayConfig(include_event_types=frozenset({EventType.TRADE}))).run(events, Strategy())
    assert histories == [()]


def test_rejects_out_of_order_source_events():
    events = [MarketEvent(2, "NIFTY", EventType.QUOTE, {}), MarketEvent(1, "NIFTY", EventType.QUOTE, {})]
    with pytest.raises(ValueError, match="ordered"):
        EventBacktestEngine().run(events, lambda event, state: None)


def test_margin_call_blocks_new_risk_after_mark_to_market_breach():
    class Strategy:
        strategy_id = "margin-block"; strategy_version = "1"
        def on_event(self, event, context):
            if event.timestamp_ns == 1_000:
                return StrategyDecision(action="BUY", orders=(SimOrder("open", "X", ExecutionSide.BUY, 300),))
            return StrategyDecision(action="BUY", orders=(SimOrder("add", "X", ExecutionSide.BUY, 1),))

    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([
        MarketEvent(1_000, "X", EventType.QUOTE, {"bid": 499, "ask": 500}),
        MarketEvent(2_000, "X", EventType.QUOTE, {"bid": 99, "ask": 100}),
    ], Strategy())
    assert result.fills == 1
    assert result.risk_blocks == 1
    assert result.final_snapshot.positions[0].quantity == 300
    assert engine.order_states["add"].status == OrderStatus.REJECTED


def test_margin_call_still_allows_risk_reducing_close_order():
    class Strategy:
        strategy_id = "margin-unwind"; strategy_version = "1"
        def on_event(self, event, context):
            if event.timestamp_ns == 1_000:
                return StrategyDecision(action="BUY", orders=(SimOrder("open", "X", ExecutionSide.BUY, 300),))
            return StrategyDecision(action="SELL", orders=(SimOrder("close", "X", ExecutionSide.SELL, 300),))

    portfolio = Portfolio(100_000, RiskConfig(initial_margin_rate=0.5, maintenance_margin_rate=0.4))
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    result = engine.run([
        MarketEvent(1_000, "X", EventType.QUOTE, {"bid": 499, "ask": 500}),
        MarketEvent(2_000, "X", EventType.QUOTE, {"bid": 100, "ask": 101}),
    ], Strategy())
    assert result.risk_blocks == 0
    assert result.fills == 2
    assert result.final_snapshot.positions == ()
