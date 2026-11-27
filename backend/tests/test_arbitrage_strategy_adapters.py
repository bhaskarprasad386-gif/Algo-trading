from app.backtesting.arbitrage_strategy_adapters import BoxSpreadStrategyAdapter


def option(ts, strike, cb, ca, pb, pa, expiry=20261231):
    return {
        "timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "strike": strike,
        "call_bid": cb, "call_ask": ca, "put_bid": pb, "put_ask": pa,
    }


def test_box_adapter_closes_only_on_later_reverse_edge():
    adapter = BoxSpreadStrategyAdapter(fees_per_unit=1.0)
    low = option(1, 100, 6, 7, 5, 6)
    high = option(1, 110, 2, 3, 4, 5)
    entries = tuple(adapter.entry({"low": low, "high": high, "data_resolution": "1s"}))
    assert len(entries) == 1
    assert entries[0].entry_price == 5.0
    assert adapter.exit(entries[0], {"low": low, "high": high}) is None

    low2 = option(3, 100, 20, 21, 20, 21)
    high2 = option(3, 110, 1, 2, 20, 21)
    close = adapter.exit(entries[0], {"low": low2, "high": high2})
    assert close is not None
    assert close.gross_pnl == 12.0
    assert close.fees == 2.0


def test_box_adapter_requires_real_quote_payloads():
    adapter = BoxSpreadStrategyAdapter()
    try:
        tuple(adapter.entry({"low": {}}))
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("invalid quote payload must be rejected")
