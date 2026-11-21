from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.scanner.early_impulse_shoonya_cache import EarlyImpulseShoonyaCache


def _record(ts=1_700_000_000_000_000_000, token="123"):
    return MarketDataRecord(
        instrument=InstrumentKey("NSE", "NFO", token),
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=ts,
        ltp=22000.0,
        bid=21999.9,
        ask=22000.1,
        bid_qty=100,
        ask_qty=120,
    )


def test_accepts_authenticated_tick_and_reports_fresh_quote():
    cache = EarlyImpulseShoonyaCache(capacity=3)
    assert cache.on_tick_observation(_record(), 2, True, 5_000_000_000)
    snap = cache.snapshot(now_monotonic_ns=5_500_000_000)
    assert snap["status"] == "LIVE"
    assert snap["fresh_count"] == 1
    assert snap["quotes"][0]["symbol"] == "NIFTY"
    assert snap["order_routing_enabled"] is False


def test_rejects_unauthenticated_tick():
    cache = EarlyImpulseShoonyaCache()
    assert cache.on_tick_observation(_record(), 1, False, 1_000_000_000) is False
    assert cache.snapshot(now_monotonic_ns=1_100_000_000)["rejected_count"] == 1


def test_rejects_duplicate_and_out_of_order_exchange_timestamps():
    cache = EarlyImpulseShoonyaCache()
    assert cache.on_tick_observation(_record(ts=100), 1, True, 10_000_000_000)
    assert not cache.on_tick_observation(_record(ts=100), 1, True, 10_100_000_000)
    assert not cache.on_tick_observation(_record(ts=90), 1, True, 10_200_000_000)
    assert cache.snapshot(now_monotonic_ns=10_300_000_000)["rejected_count"] == 2


def test_stale_quote_is_not_exposed_as_fresh():
    cache = EarlyImpulseShoonyaCache(max_age_seconds=1)
    assert cache.on_tick_observation(_record(), 3, True, 1_000_000_000)
    snap = cache.snapshot(now_monotonic_ns=2_100_000_000)
    assert snap["fresh_count"] == 0
    assert snap["stale_count"] == 1


def test_disconnect_invalidates_fresh_signal_data():
    cache = EarlyImpulseShoonyaCache()
    assert cache.on_tick_observation(_record(), 4, True, 1_000_000_000)
    cache.on_session_observation("DISCONNECTED", "network", 4, False, False, 1_100_000_000)
    snap = cache.snapshot(now_monotonic_ns=1_200_000_000)
    assert snap["status"] == "DISCONNECTED"
    assert snap["fresh_count"] == 0


def test_cache_capacity_is_bounded():
    cache = EarlyImpulseShoonyaCache(capacity=2)
    for i in range(3):
        assert cache.on_tick_observation(_record(ts=100+i, token=str(i)), 1, True, 2_000_000_000+i)
    assert cache.snapshot(now_monotonic_ns=2_100_000_000)["fresh_count"] == 2


def test_invalid_capacity_rejected():
    try:
        EarlyImpulseShoonyaCache(capacity=0)
    except ValueError:
        pass
    else:
        raise AssertionError("capacity zero must be rejected")
