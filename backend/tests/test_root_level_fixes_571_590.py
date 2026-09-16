from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.backtesting.engine import BacktestConfig, BacktestEngine, EventSignal
from app.backtesting.root_level_fixes_571_590 import cagr, event_sort_key


def test_cagr_uses_actual_interval():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert cagr(100.0, 110.0, start, end) == pytest.approx(0.0997, rel=1e-3)


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
        SimpleNamespace(timestamp_ns=1, sequence=0, source="test", instrument="X", timeframe="tick", payload={"price": 90}),
        SimpleNamespace(timestamp_ns=2, sequence=0, source="test", instrument="X", timeframe="tick", payload={"price": 100}),
    ]
    result = engine.run_events(events, lambda c: EventSignal("BUY", 95) if c.timestamp_ns == 1 else EventSignal("SELL", 100))
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 95
    assert result.trades[0].exit_price == 100
