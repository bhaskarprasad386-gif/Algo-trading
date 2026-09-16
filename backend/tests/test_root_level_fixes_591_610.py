import math

import pytest

from app.backtesting.arbitrage_backtester import FutureQuote, LiquidityPolicy, OptionQuote
from app.backtesting.events import EventReplayConfig, EventType, MarketEvent
from app.backtesting.execution import DepthLevel, ExecutionConfig, ExecutionSide, OrderType, SimFill, SimOrder
from app.backtesting.order_lifecycle import OrderLifecycle, OrderStatus, stop_triggered
from app.backtesting.universal import MarketEvent as LegacyMarketEvent, ordered_events, streaming_events, validate_source_resolution


def test_591_legacy_market_event_rejects_blank_instrument():
    with pytest.raises(ValueError):
        LegacyMarketEvent(1, "   ")


def test_592_legacy_market_event_rejects_blank_event_type():
    with pytest.raises(ValueError):
        LegacyMarketEvent(1, "SBIN", event_type=" ")


def test_593_legacy_market_event_rejects_malformed_data_entry():
    with pytest.raises(TypeError):
        LegacyMarketEvent(1, "SBIN", data=(("price", 100), ("bad",)))


def test_594_legacy_ordering_breaks_same_timestamp_sequence_ties_deterministically():
    events = [LegacyMarketEvent(1, "B", event_type="quote"), LegacyMarketEvent(1, "A", event_type="trade")]
    assert [e.instrument for e in ordered_events(events)] == ["A", "B"]


def test_595_legacy_streaming_rejects_non_total_order():
    events = [LegacyMarketEvent(2, "A"), LegacyMarketEvent(1, "A")]
    with pytest.raises(ValueError):
        tuple(streaming_events(events))


def test_596_resolution_check_uses_ordered_stream_contract():
    events = [LegacyMarketEvent(0, "A"), LegacyMarketEvent(1_000, "A")]
    validate_source_resolution(events, 1_000)


def test_597_event_rejects_non_string_instrument():
    with pytest.raises(ValueError):
        MarketEvent(1, "   ", EventType.QUOTE)


def test_598_event_rejects_non_event_type():
    with pytest.raises(TypeError):
        MarketEvent(1, "SBIN", "quote")


def test_599_event_rejects_blank_source():
    with pytest.raises(ValueError):
        MarketEvent(1, "SBIN", EventType.QUOTE, source=" ")


def test_600_depth_queue_evidence_rejects_non_executable_price():
    with pytest.raises(ValueError):
        MarketEvent(1, "SBIN", EventType.DEPTH, {"queue_evidence": [{"price": 0}]})


def test_601_replay_config_rejects_invalid_event_filter():
    with pytest.raises(TypeError):
        EventReplayConfig(include_event_types=frozenset({"quote"}))


def test_602_replay_config_rejects_negative_timestamp_conversion():
    with pytest.raises(ValueError):
        EventReplayConfig(timestamp_unit="ms").to_ns(-1)


def test_603_order_rejects_boolean_submission_timestamp():
    with pytest.raises(ValueError):
        SimOrder("o", "SBIN", ExecutionSide.BUY, 1, submitted_at_ns=True)


def test_604_order_rejects_boolean_limit_price():
    with pytest.raises(ValueError):
        SimOrder("o", "SBIN", ExecutionSide.BUY, 1, OrderType.LIMIT, limit_price=True)


def test_605_depth_level_rejects_boolean_price():
    with pytest.raises(ValueError):
        DepthLevel(True, 1)


def test_606_fill_rejects_boolean_timestamp():
    with pytest.raises(ValueError):
        SimFill("o", "SBIN", ExecutionSide.BUY, 1, 100.0, True)


def test_607_execution_config_requires_integer_latency():
    with pytest.raises(ValueError):
        ExecutionConfig(latency_ns=1.5)


def test_608_lifecycle_requires_reason_and_valid_checkpoint_history():
    lifecycle = OrderLifecycle(SimOrder("o", "SBIN", ExecutionSide.BUY, 1))
    lifecycle.accept(0)
    with pytest.raises(ValueError):
        lifecycle.cancel(1, " ")
    raw = lifecycle.export_state()
    raw["status"] = OrderStatus.FILLED.value
    with pytest.raises(ValueError):
        OrderLifecycle.restore_state(raw)


def test_609_stop_trigger_rejects_nan_market_price():
    order = SimOrder("o", "SBIN", ExecutionSide.BUY, 1, OrderType.STOP, stop_price=100.0)
    with pytest.raises(ValueError):
        stop_triggered(order, math.nan)


def test_610_arbitrage_rejects_zero_ask_as_non_executable():
    quote = OptionQuote(1, "SBIN", 202610, 100.0, 1.0, 0.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        from app.backtesting.arbitrage_backtester import BoxSpreadBacktester
        BoxSpreadBacktester.evaluate(quote, OptionQuote(1, "SBIN", 202610, 110.0, 1.0, 1.0, 1.0, 1.0))
