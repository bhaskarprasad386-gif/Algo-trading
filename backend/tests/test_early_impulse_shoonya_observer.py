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


def test_runner_rejects_non_finite_prices_before_history_ingestion():
    from app.scanner.early_impulse_shoonya_runner import EarlyImpulseLiveRunner
    runner = EarlyImpulseLiveRunner()
    roles = {
        "NSE:NFO:101": "spot",
        "NSE:NFO:102": "future",
        "NSE:NFO:103": "cross",
    }
    runner.configure_roles(roles)
    runner.on_session("LIVE", 1, True, True)
    runner.enable()
    for invalid_price in (float("nan"), float("inf"), float("-inf")):
        record = _record()
        record.ltp = invalid_price
        assert runner.on_tick(record, 1, True, monotonic_ns()) is None
    assert runner.snapshot()["history_sizes"] == {}


def test_runner_rejects_duplicate_and_out_of_order_ticks_without_volume_corruption():
    from app.scanner.early_impulse_shoonya_runner import EarlyImpulseLiveRunner
    runner = EarlyImpulseLiveRunner()
    roles = {
        "NSE:NFO:101": "spot",
        "NSE:NFO:102": "future",
        "NSE:NFO:103": "cross",
    }
    runner.configure_roles(roles)
    runner.on_session("LIVE", 1, True, True)
    runner.enable()
    record = _record()
    record.volume = 100
    assert runner.on_tick(record, 1, True, 1_000_000_000) is None
    record.volume = 150
    assert runner.on_tick(record, 1, True, 2_000_000_000) is None
    record.volume = 125
    assert runner.on_tick(record, 1, True, 1_500_000_000) is None
    record.volume = 160
    assert runner.on_tick(record, 1, True, 2_000_000_000) is None
    record.volume = 180
    assert runner.on_tick(record, 1, True, 3_000_000_000) is None
    quotes = list(runner._history["NSE:NFO:101"])
    assert [q.timestamp_ns for q in quotes] == [1_000_000_000, 2_000_000_000, 3_000_000_000]
    assert [q.volume_delta for q in quotes] == [None, 50, 30]


def test_runner_does_not_rewind_global_eval_clock_for_cross_role_tick():
    from app.scanner.early_impulse_shoonya_runner import EarlyImpulseLiveRunner
    runner = EarlyImpulseLiveRunner()
    runner.configure_roles({
        "NSE:NFO:101": "spot", "NSE:NFO:102": "future", "NSE:NFO:103": "cross",
    })
    runner.on_session("LIVE", 1, True, True)
    runner.enable()
    runner._last_eval_ns = 2_000_000_000
    record = _record()
    assert runner.on_tick(record, 1, True, 1_900_000_000) is None
    assert runner._last_eval_ns == 2_000_000_000


def test_runner_rejects_tick_marked_unauthenticated_even_after_live_session():
    from app.scanner.early_impulse_shoonya_runner import EarlyImpulseLiveRunner
    runner = EarlyImpulseLiveRunner()
    runner.configure_roles({
        "NSE:NFO:101": "spot", "NSE:NFO:102": "future", "NSE:NFO:103": "cross",
    })
    runner.on_session("LIVE", 3, True, True)
    runner.enable()
    record = _record()
    assert runner.on_tick(record, 3, False, monotonic_ns()) is None
    assert runner.snapshot()["history_sizes"] == {}


def test_runner_ignores_old_epoch_disconnect_after_new_epoch_live():
    from app.scanner.early_impulse_shoonya_runner import EarlyImpulseLiveRunner
    runner = EarlyImpulseLiveRunner()
    runner.on_session("LIVE", 5, True, True)
    runner.on_session("RECONNECTING", 4, False, False)
    snap = runner.snapshot()
    assert runner._epoch == 5
    assert runner._authenticated is True


def test_cache_tick_failure_does_not_block_runner_tick():
    class RunnerSpy:
        def __init__(self):
            self.ticks = []
            self.sessions = []
        def on_tick(self, *args):
            self.ticks.append(args)
        def on_session(self, *args):
            self.sessions.append(args)
        def snapshot(self):
            return {}

    cache = EarlyImpulseShoonyaCache()
    cache.on_tick_observation = lambda *args: (_ for _ in ()).throw(RuntimeError("cache tick failure"))
    runner = RunnerSpy()
    observer = EarlyImpulseShoonyaObserver(cache=cache, runner=runner)
    record = _record()
    observer.on_tick_observation(record, 4, True, 500)
    assert len(runner.ticks) == 1
    assert runner.ticks[0] == (record, 4, True, 500)
    assert observer.snapshot()["observer_errors"] == 1


def test_cache_session_failure_does_not_block_runner_session_reset():
    class RunnerSpy:
        def __init__(self):
            self.sessions = []
        def on_tick(self, *args):
            pass
        def on_session(self, *args):
            self.sessions.append(args)
        def snapshot(self):
            return {}

    cache = EarlyImpulseShoonyaCache()
    cache.on_session_observation = lambda *args: (_ for _ in ()).throw(RuntimeError("cache session failure"))
    runner = RunnerSpy()
    observer = EarlyImpulseShoonyaObserver(cache=cache, runner=runner)
    observer.on_session_observation("RECONNECTING", "disconnect", 8, False, False, 900)
    assert runner.sessions == [("RECONNECTING", 8, False, False)]
    assert observer.snapshot()["observer_errors"] == 1
