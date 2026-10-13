from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentKey
from app.market_data.shared_cache import get_shared_market_data_cache, market_data_cache_key


def test_strategy_feed_reads_latest_snapshot_by_canonical_instrument_key():
    cache = get_shared_market_data_cache()
    instrument = InstrumentKey("nfo", "nfo", "12345")
    cache_key = market_data_cache_key("NFO", "NFO", "12345")
    cache.delete(cache_key)
    feed = CommonStrategyMarketFeed("cache-reader", manager=object())

    try:
        assert feed.latest_snapshot(instrument) is None
        cache.put(cache_key, {"exchange": "NFO", "segment": "NFO", "token": "12345", "ltp": 210.5})
        assert feed.latest_snapshot(instrument) == {
            "exchange": "NFO", "segment": "NFO", "token": "12345", "ltp": 210.5
        }
    finally:
        cache.delete(cache_key)


def test_strategy_feed_rejects_non_instrument_cache_lookup():
    feed = CommonStrategyMarketFeed("cache-reader", manager=object())
    try:
        feed.latest_snapshot("NFO:NFO:12345")
    except TypeError as exc:
        assert "InstrumentKey" in str(exc)
    else:
        raise AssertionError("non-InstrumentKey lookup should be rejected")
