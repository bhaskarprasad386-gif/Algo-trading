from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.backtesting.engine import BacktestConfig, BacktestEngine, EventSignal
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.root_level_fixes_571_590 import cagr, event_sort_key


def test_cagr_uses_actual_interval():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert cagr(100.0, 110.0, start, end) == pytest.approx(0.10006965697379555, rel=1e-6)


def test_cagr_rejects_non_positive_equity_and_zero_duration():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert cagr(100.0, 0.0, start, start) is None


def test_event_order_has_deterministic_stream_tie_breakers():
    a = SimpleNamespace(timestamp_ns=10, sequence=1, source="b", instrument="X", timeframe="1s")
    b = SimpleNamespace(timestamp_ns=10, sequence=1, source="a", instrument="X", timeframe="1s")
    assert event_sort_key(b) < event_sort_key(a)


def test_event_signal_price_is_used_when_present():
    engine = BacktestEngine(BacktestConfig(quantity=1))
    events = [
        HistoricalRecord("test", "X", "tick", 1, {"price": 90}, 0),
        HistoricalRecord("test", "X", "tick", 2, {"price": 100}, 0),
    ]
    result = engine.run_events(events, lambda c: EventSignal("BUY", 95) if c.timestamp_ns == 1 else EventSignal("SELL", 100))
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 95
    assert result.trades[0].exit_price == 100


def test_entry_cost_is_reserved_and_total_cost_is_not_double_charged():
    config = BacktestConfig(initial_capital=1000.0, transaction_cost_rate=0.01)
    engine = BacktestEngine(config)
    events = [
        SimpleNamespace(timestamp_ns=1, sequence=0, source="test", instrument="X", timeframe="tick", payload={"price": 100}),
        SimpleNamespace(timestamp_ns=2, sequence=0, source="test", instrument="X", timeframe="tick", payload={"price": 110}),
    ]
    result = engine.run_events(events, lambda c: EventSignal("BUY") if c.timestamp_ns == 1 else EventSignal("SELL"))
    assert result.trades[0].gross_pnl == pytest.approx(10.0)
    assert result.trades[0].costs == pytest.approx(2.1)
    assert result.trades[0].net_pnl == pytest.approx(7.9)
    assert result.final_capital == pytest.approx(1007.9)
    assert result.liquidation_value == pytest.approx(result.final_capital)


def test_open_position_result_exposes_executable_liquidation_value():
    config = BacktestConfig(initial_capital=1000.0, transaction_cost_rate=0.01)
    engine = BacktestEngine(config)
    events = [
        SimpleNamespace(timestamp_ns=1, sequence=0, source="test", instrument="X", timeframe="tick", payload={"price": 100}),
        SimpleNamespace(timestamp_ns=2, sequence=0, source="test", instrument="X", timeframe="tick", payload={"price": 110}),
    ]
    result = engine.run_events(events, lambda c: EventSignal("BUY"))
    assert result.has_open_trade is True
    assert result.unrealized_pnl == pytest.approx(8.9)
    assert result.final_capital == pytest.approx(1007.9)
    assert result.liquidation_value == pytest.approx(1007.9)
