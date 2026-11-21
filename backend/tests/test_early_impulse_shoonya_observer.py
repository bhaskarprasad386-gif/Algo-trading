from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.provider_readiness_bridge import ProviderReadinessBridge
from app.scanner.early_impulse_shoonya_cache import EarlyImpulseShoonyaCache
from app.scanner.early_impulse_shoonya_observer import EarlyImpulseShoonyaObserver
from time import time_ns, monotonic_ns


def _record():
    return MarketDataRecord(
        instrument=InstrumentKey("NSE", "NFO", "101"),
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        timestamp_ns=time_ns(),
        ltp=22000, bid=21999, ask=22001, bid_qty=10, ask_qty=20,
    )


def test_observer_fanout_preserves_readiness_and_cache_data():
    cache = EarlyImpulseShoonyaCache()
    readiness = ProviderReadinessBridge()
    observer = EarlyImpulseShoonyaObserver(readiness=readiness, cache=cache)
    observer.on_session_observation("LIVE", None, 1, True, True, 100)
    observer.on_tick_observation(_record(), 1, True, 200)
    assert cache.snapshot(now_monotonic_ns=300)["fresh_count"] == 1
    assert readiness.snapshot()["shoonya"]["canonical_identity_valid"] is True
    assert readiness.snapshot()["shoonya"]["exchange_timestamp_valid"] is True


def test_observer_errors_are_isolated_and_counted():
    class BadReadiness:
        def shoonya_state(self, *args): raise RuntimeError("state observer failure")
        def shoonya_market_event(self, *args): raise RuntimeError("tick observer failure")
    observer = EarlyImpulseShoonyaObserver(readiness=BadReadiness(), cache=EarlyImpulseShoonyaCache())
    observer.on_session_observation("LIVE", None, 1, True, True, 100)
    observer.on_tick_observation(_record(), 1, True, 200)
    assert observer.snapshot()["observer_errors"] == 2
    assert observer.snapshot()["order_routing_enabled"] is False


def test_disconnect_and_epoch_reset_clear_cached_quotes():
    cache = EarlyImpulseShoonyaCache()
    observer = EarlyImpulseShoonyaObserver(readiness=ProviderReadinessBridge(), cache=cache)
    now = monotonic_ns()
    observer.on_session_observation("LIVE", None, 1, True, True, now)
    observer.on_tick_observation(_record(), 1, True, now + 1)
    assert observer.snapshot()["fresh_count"] == 1
    observer.on_session_observation("RECONNECTING", "disconnect", 2, False, False, now + 2)
    assert observer.snapshot()["fresh_count"] == 0
    observer.on_session_observation("LIVE", None, 2, True, True, now + 3)
    assert observer.snapshot()["feed_epoch"] == 2
    assert observer.snapshot()["fresh_count"] == 0

def test_auth_pending_session_does_not_allow_tick_into_cache():
    cache = EarlyImpulseShoonyaCache()
    observer = EarlyImpulseShoonyaObserver(readiness=ProviderReadinessBridge(), cache=cache)
    observer.on_session_observation("AUTHENTICATING", None, 7, True, False, 100)
    observer.on_tick_observation(_record(), 7, True, 200)
    snap = observer.snapshot()
    assert snap["status"] == "AUTH_PENDING"
    assert snap["fresh_count"] == 0
    assert snap["rejected_count"] == 1


def test_runner_evaluation_throttle_resets_on_feed_epoch_and_disable():
    from app.scanner.early_impulse_shoonya_runner import EarlyImpulseLiveRunner
    runner = EarlyImpulseLiveRunner()
    runner._last_eval_ns = 987654321
    runner.on_session("LIVE", 1, True, True)
    assert runner._last_eval_ns == 0
    runner._last_eval_ns = 987654321
    runner.on_session("RECONNECTING", 2, False, False)
    assert runner._last_eval_ns == 0
    runner._last_eval_ns = 987654321
    runner.disable()
    assert runner._last_eval_ns == 0
