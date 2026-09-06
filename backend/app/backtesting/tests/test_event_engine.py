from dataclasses import dataclass

import pytest

from app.backtesting.event_engine import EventBacktestEngine
from app.backtesting.events import EventReplayConfig, EventType, MarketEvent
from app.backtesting.execution import ExecutionSimulator, ExecutionSide, SimOrder
from app.backtesting.portfolio import Portfolio
from app.backtesting.strategy import StrategyContext, StrategyDecision


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


def test_multi_leg_orders_use_independent_point_in_time_quotes():
    class Strategy:
        strategy_id = "multi-leg"; strategy_version = "1"
        def on_event(self, event, context):
            if event.instrument != "FUT_FAR": return None
            return StrategyDecision(action="SPREAD", orders=(
                SimOrder("leg1", "FUT_NEAR", ExecutionSide.BUY, 1),
                SimOrder("leg2", "FUT_FAR", ExecutionSide.SELL, 1),
            ))
    portfolio = Portfolio(initial_cash=100_000)
    engine = EventBacktestEngine(execution=ExecutionSimulator(), portfolio=portfolio)
    events = [
        MarketEvent(1_000, "FUT_NEAR", EventType.QUOTE, {"bid": 99, "ask": 101}),
        MarketEvent(2_000, "FUT_FAR", EventType.QUOTE, {"bid": 205, "ask": 207}),
    ]
    result = engine.run(events, Strategy())
    assert result.orders_submitted == 2 and result.fills == 2
    assert {p.instrument for p in result.final_snapshot.positions} == {"FUT_NEAR", "FUT_FAR"}
    assert result.final_snapshot.cash == pytest.approx(100_000 - 101 + 205)


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
