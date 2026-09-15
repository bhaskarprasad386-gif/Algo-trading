from math import inf

import pytest

from app.backtesting.arbitrage_backtester import LiquidityPolicy, OptionQuote
from app.backtesting.arbitrage_strategy_adapters import (
    BoxSpreadStrategyAdapter, CalendarSpreadStrategyAdapter, CashFutureStrategyAdapter,
    SyntheticCashCarryStrategyAdapter,
)


def option(ts, strike, cb, ca, pb, pa, expiry=20261231, volume=0, oi=0):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "strike": strike,
            "call_bid": cb, "call_ask": ca, "put_bid": pb, "put_ask": pa,
            "volume": volume, "oi": oi}


def future(ts, bid, ask, expiry=20261231, volume=0, oi=0):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "bid": bid, "ask": ask,
            "volume": volume, "oi": oi}


def calendar(ts, expiry, bid, ask):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "bid": bid, "ask": ask,
            "strike": 100.0, "option_type": "CALL"}


def test_box_adapter_closes_only_on_later_reverse_edge():
    adapter = BoxSpreadStrategyAdapter(fees_per_unit=1.0)
    low = option(1, 100, 6, 4, 5, 3)
    high = option(1, 110, 2, 3, 2, 2)
    entries = tuple(adapter.entry({"low": low, "high": high, "data_resolution": "1s"}))
    assert len(entries) == 1
    assert entries[0].entry_price == 3.0
    assert entries[0].entry_fees == 1.0
    assert adapter.exit(entries[0], {"low": low, "high": high}) is None

    low2 = option(3, 100, 15, 16, 14, 15)
    high2 = option(3, 110, 3, 4, 3, 4)
    close = adapter.exit(entries[0], {"low": low2, "high": high2})
    assert close is not None
    assert close.gross_pnl == 14.0
    assert close.fees == 1.0


def test_synthetic_adapter_preserves_expiry_and_later_exit():
    adapter = SyntheticCashCarryStrategyAdapter(fees_per_unit=0.5)
    event = {"option": option(1, 100, 10, 11, 1, 2), "future": future(1, 111, 112)}
    entries = tuple(adapter.entry(event))
    assert len(entries) == 1
    assert entries[0].expiry == "20261231"
    assert entries[0].entry_fees == 0.5
    later = {"option": option(2, 100, 12, 13, 1, 2), "future": future(2, 101, 102)}
    close = adapter.exit(entries[0], later)
    assert close is not None
    assert close.timestamp_ns == 2
    assert close.fees == 0.5


def test_cash_future_adapter_supports_both_directions_without_fabrication():
    event = {"cash_future": {"timestamp_ns": 1, "underlying": "ABC", "spot_bid": 100,
        "spot_ask": 101, "future_bid": 104, "future_ask": 105, "expiry": 20261231,
        "lot_size": 10, "carry_factor": 1.0}}
    adapter = CashFutureStrategyAdapter(fees_per_unit=0.25)
    entries = tuple(adapter.entry(event))
    assert len(entries) == 1
    assert entries[0].entry_price == 3
    assert entries[0].entry_fees == 2.5
    assert adapter.exit(entries[0], event) is None
    later = {"cash_future": {**event["cash_future"], "timestamp_ns": 2,
        "spot_bid": 106, "spot_ask": 107, "future_bid": 102, "future_ask": 103}}
    close = adapter.exit(entries[0], later)
    assert close is not None
    assert close.gross_pnl == 6
    assert close.fees == 2.5


def test_calendar_adapter_keeps_near_and_far_expiries_explicit():
    adapter = CalendarSpreadStrategyAdapter(fees_per_unit=0.5)
    event = {"near": calendar(1, 20260924, 10, 11), "far": calendar(1, 20261029, 15, 16)}
    entries = tuple(adapter.entry(event))
    assert len(entries) == 1
    assert entries[0].expiry == "20260924"
    assert entries[0].metadata["far_expiry"] == 20261029
    assert entries[0].entry_fees == 0.5
    later = {"near": calendar(3, 20260924, 14, 15), "far": calendar(3, 20261029, 12, 13)}
    close = adapter.exit(entries[0], later)
    assert close is not None
    assert close.timestamp_ns == 3
    assert close.gross_pnl == 5
    assert close.fees == 0.5


def test_adapters_require_real_quote_payloads():
    adapter = CalendarSpreadStrategyAdapter()
    with pytest.raises((TypeError, ValueError)):
        tuple(adapter.entry({"near": {}}))


def test_box_liquidity_applies_to_all_option_legs():
    policy = LiquidityPolicy(min_option_volume=100, min_option_oi=10, max_spread_pct=10)
    low = option(1, 100, 10, 10.5, 8, 8.4, volume=100, oi=10)
    high = option(1, 110, 2, 2.1, 1, 1.1, volume=99, oi=10)
    adapter = BoxSpreadStrategyAdapter()
    assert tuple(adapter.entry({"low": low, "high": high, "liquidity": policy})) == ()


def test_synthetic_liquidity_applies_to_option_and_future():
    policy = LiquidityPolicy(min_option_volume=100, min_future_volume=100)
    opt = option(1, 100, 10, 11, 1, 2, volume=99, oi=100)
    fut = future(1, 111, 112, volume=100)
    adapter = SyntheticCashCarryStrategyAdapter()
    assert tuple(adapter.entry({"option": opt, "future": fut, "liquidity": policy})) == ()


def test_synthetic_rejects_non_finite_time_to_expiry():
    with pytest.raises(ValueError):
        SyntheticCashCarryStrategyAdapter(time_to_expiry_years=inf)


def test_liquidity_policy_rejects_invalid_spread_limit():
    with pytest.raises(ValueError):
        LiquidityPolicy(max_spread_pct=-1)


def test_option_quote_rejects_negative_volume_and_oi():
    with pytest.raises(ValueError):
        OptionQuote(timestamp_ns=1, underlying="ABC", expiry=20261231, strike=100,
                    call_bid=1, call_ask=2, put_bid=1, put_ask=2, volume=-1)
