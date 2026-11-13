from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.scanner.iv_skew_flip.runtime import CHAIN_CONFIGS
from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime, _universe_selection_date
from app.scanner.iv_skew_flip.sampler import SpotSnapshot


IST = ZoneInfo("Asia/Kolkata")


class FakeSpotFeed:
    def __init__(self):
        self.prices = {
            "NIFTY": SpotSnapshot(100.0, 10_000_000_000),
            "BANKNIFTY": SpotSnapshot(200.0, 10_000_000_000),
        }
        self.last_prices = dict(self.prices)

    def spot_snapshot(self, symbol):
        return self.prices.get(symbol)

    def last_spot_snapshot(self, symbol):
        return self.last_prices.get(symbol)

    def snapshot(self):
        return {"status": "LIVE", "spot": {k: v.price for k, v in self.prices.items()}}

    def stop(self):
        pass


class FakeOptionFeed:
    def __init__(self, symbol):
        self.symbol = symbol
        self.universe = None
        self.started = False
        self.start_calls = 0

    def start(self, universe):
        self.start_calls += 1
        self.universe = universe
        self.started = True
        return {"consumer": f"iv-skew-flip-{self.symbol.lower()}"}

    def snapshot(self):
        return {"status": "LIVE" if self.started else "STOPPED"}

    def stop(self):
        self.started = False


class FakeSampler:
    def __init__(self, **kwargs):
        self.quote_source = kwargs["quote_source"]
        self.spot_provider = kwargs["spot_provider"]
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def inherit_history(self, previous, universe):
        self.inherited_from = previous
        self.inherited_universe = universe
        return 0

    def snapshot(self):
        spot = self.spot_provider()
        return {"status": "LIVE" if self.started and spot else "WAITING"}


class FakeMaster:
    def __init__(self, rows):
        self.instruments = rows

    def download(self):
        return self.instruments

    def resolve_index_instrument(self, symbol, exchange):
        return {"token": "99926000" if symbol == "NIFTY" else "99926009", "symbol": symbol}


def make_rows(symbol, expiry, base, step):
    rows = []
    for strike in [base + step * i for i in range(-10, 11)]:
        for side in ("CE", "PE"):
            rows.append({
                "exch_seg": "NFO",
                "instrumenttype": "OPTIDX",
                "name": symbol,
                "expiry": expiry,
                "symbol": f"{symbol}{expiry}{int(strike)}{side}",
                "token": f"{symbol}-{int(strike)}-{side}",
                "lotsize": "25",
                "strike": str(int(strike * 100)),
            })
    return rows


def test_runtime_rebuilds_only_changed_strike_universe(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    rows = (
        make_rows("NIFTY", "12NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 200, 10)
    )
    spot = FakeSpotFeed()
    created = {}

    def factory(symbol):
        created[symbol] = FakeOptionFeed(symbol)
        return created[symbol]

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=spot,
        option_feed_factory=factory,
        clock_ns=lambda: 10_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }

    first = runtime.refresh_once()
    assert first["enabled_streams"] == ["NIFTY_WEEKLY"]
    assert first["option_chains"]["NIFTY_WEEKLY"]["enabled"] is True
    assert first["option_chains"]["NIFTY_WEEKLY"]["universe_tokens"] == 22
    assert first["option_chains"]["NIFTY_MONTHLY"]["enabled"] is False
    assert first["option_chains"]["NIFTY_MONTHLY"]["option_feed"]["status"] == "DISABLED_BY_SCOPE"
    assert first["option_chains"]["BANKNIFTY_MONTHLY"]["enabled"] is False
    assert first["indices"]["BANKNIFTY"]["status"] == "DISABLED_BY_SCOPE"
    assert set(created) == {"NIFTY_WEEKLY"}
    assert created["NIFTY_WEEKLY"].universe.expiry == "2026-11-12"

    weekly_universe = created["NIFTY_WEEKLY"].universe
    runtime.refresh_once()
    assert created["NIFTY_WEEKLY"].universe is weekly_universe

    previous_sampler = runtime._samplers["NIFTY_WEEKLY"]
    spot.prices["NIFTY"] = SpotSnapshot(140.0, 11_000_000_000)
    runtime.refresh_once()
    assert created["NIFTY_WEEKLY"].universe.strikes[5] == 140.0
    assert runtime._samplers["NIFTY_WEEKLY"].inherited_from is previous_sampler


def test_runtime_waits_for_fresh_spot_before_option_subscriptions(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    rows = make_rows("NIFTY", "12NOV2026", 100, 10) + make_rows("BANKNIFTY", "26NOV2026", 200, 100)
    spot = FakeSpotFeed()
    spot.prices = {}
    spot.last_prices = {}
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows), spot_feed=spot,
        option_feed_factory=lambda symbol: FakeOptionFeed(symbol),
        clock_ns=lambda: 10_000_000_000, clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }
    result = runtime.refresh_once()
    assert result["indices"]["NIFTY"]["universe_tokens"] == 0
    assert result["indices"]["BANKNIFTY"]["status"] == "DISABLED_BY_SCOPE"
    assert "WAITING_FOR_SPOT" in result["option_chains"]["NIFTY_WEEKLY"]["error"]
    assert result["option_chains"]["NIFTY_MONTHLY"]["enabled"] is False
    assert result["option_chains"]["BANKNIFTY_MONTHLY"]["enabled"] is False
    assert all(not feed.started for feed in runtime._option_feeds.values())


def test_runtime_rejects_naive_clock():
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0),
    )
    try:
        runtime.refresh_once()
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive runtime clock must be rejected")



def test_runtime_signal_evaluation_is_rate_limited_to_one_minute():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_ns=lambda: 1_000_000_000,
        clock_datetime=lambda: now,
    )
    evaluated = []
    runtime._evaluate_symbol = lambda stream_id, config, when, stamp: (evaluated.append((stream_id, stamp)) or True)

    runtime._maybe_evaluate(now, 10_000_000_000)
    runtime._maybe_evaluate(now, 69_000_000_000)
    assert len(evaluated) == 1
    assert evaluated[0][0] == "NIFTY_WEEKLY"

    runtime._maybe_evaluate(now, 70_000_000_000)
    assert len(evaluated) == 2
    assert all(stream_id == "NIFTY_WEEKLY" for stream_id, _ in evaluated)


def test_runtime_clears_signal_state_when_spot_or_option_feed_is_unavailable():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    spot = FakeSpotFeed()
    spot.prices = {}
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=spot,
        clock_ns=lambda: 20_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._signals["NIFTY_WEEKLY"] = {
        "confluence": {"direction": "BULLISH", "status": "SIGNAL"},
        "signals_allowed": True,
    }

    assert runtime._evaluate_symbol("NIFTY_WEEKLY", CHAIN_CONFIGS[0][1], now, 20_000_000_000) is True
    result = runtime.snapshot()["signals"]["NIFTY_WEEKLY"]
    assert result["signals_allowed"] is False
    assert result["confluence"]["status"] == "WAITING"
    assert result["confluence"]["direction"] is None
    assert all(item["status"] == "WAITING" for item in result["strategies"])


def test_runtime_refresh_worker_retries_after_transient_refresh_error(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    class StopAfterTwoWaits:
        def __init__(self):
            self.waits = 0

        def is_set(self):
            return False

        def wait(self, timeout):
            self.waits += 1
            return self.waits >= 2

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    runtime._stop = StopAfterTwoWaits()
    calls = []

    def refresh():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("temporary shared feed failure")
        return {}

    runtime.refresh_once = refresh
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 0.0)
    runtime._run()

    assert len(calls) == 2
    assert "RUNTIME" not in runtime._last_error


def test_runtime_does_not_close_application_owned_shared_feed_manager():
    class SharedManager:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

        def snapshot(self):
            return {"status": "LIVE", "socket_count": 1}

    manager = SharedManager()
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        feed_manager=manager,
        close_feed_manager=False,
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )

    result = runtime.stop()

    assert result["status"] == "STOPPED"
    assert manager.close_calls == 0


def test_runtime_stop_preserves_worker_reference_while_telegram_thread_is_alive():
    class StillAliveThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    telegram_thread = StillAliveThread()
    runtime._telegram_thread = telegram_thread
    stopped_spot = []
    runtime.spot_feed.stop = lambda: stopped_spot.append(True)

    pending = runtime.stop()

    assert pending["status"] == "STOPPING"
    assert runtime._stopping is True
    assert runtime._telegram_thread is telegram_thread
    assert stopped_spot == []


def test_runtime_stop_preserves_resources_while_refresh_worker_is_alive():
    class DelayedWorker:
        def __init__(self):
            self.alive = True
            self.joined = False

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.joined = True

    class TrackedFeed(FakeSpotFeed):
        def __init__(self):
            super().__init__()
            self.stopped = False

        def stop(self):
            self.stopped = True

    spot = TrackedFeed()
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=spot,
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    worker = DelayedWorker()
    runtime._started = True
    runtime._thread = worker

    pending = runtime.stop()
    assert pending["status"] == "STOPPING"
    assert runtime._started is True
    assert runtime._thread is worker
    assert spot.stopped is False

    worker.alive = False
    stopped = runtime.stop()
    assert stopped["status"] == "STOPPED"
    assert runtime._started is False
    assert runtime._thread is None
    assert spot.stopped is True

def test_runtime_stop_attempts_every_resource_and_retries_failed_cleanup():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: now,
    )
    calls = {"sampler": 0, "option": 0, "spot": 0, "equity": 0}

    class StopResource:
        def __init__(self, key, fail_first=False):
            self.key = key
            self.fail_first = fail_first

        def stop(self):
            calls[self.key] += 1
            if self.fail_first and calls[self.key] == 1:
                raise RuntimeError("temporary shutdown failure")

        def snapshot(self):
            return {"status": "STOPPED"}

    class StopSpotFeed(FakeSpotFeed):
        def stop(self):
            calls["spot"] += 1
            if calls["spot"] == 1:
                raise RuntimeError("temporary spot shutdown failure")

    runtime._started = True
    runtime._samplers = {"NIFTY_WEEKLY": StopResource("sampler")}
    runtime._option_feeds = {"NIFTY_WEEKLY": StopResource("option")}
    runtime.spot_feed = StopSpotFeed()
    runtime._equity_feed = StopResource("equity", fail_first=True)

    first = runtime.stop()
    assert first["status"] == "STOPPING"
    assert "spot_feed" in first["cleanup_errors"]
    assert "equity_feed" in first["cleanup_errors"]
    assert calls == {"sampler": 1, "option": 1, "spot": 1, "equity": 1}
    # References remain available, and all independent resources were tried.
    assert runtime._started is True
    assert runtime._samplers
    assert runtime._option_feeds

    second = runtime.stop()
    assert second["status"] == "STOPPED"
    assert second["cleanup_errors"] is None
    assert calls == {"sampler": 2, "option": 2, "spot": 2, "equity": 2}
    assert runtime._samplers == {}
    assert runtime._option_feeds == {}
    assert runtime._equity_feed is None


def test_runtime_start_retains_partially_registered_spot_feed_when_rollback_fails(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    rows = (
        make_rows("NIFTY", "12NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 200, 10)
    )
    monkeypatch.setattr(runtime_module, "download_iv_instruments", lambda master: rows)

    class PartiallyFailingSpotFeed(FakeSpotFeed):
        def __init__(self):
            super().__init__()
            self.start_calls = 0
            self.stop_calls = 0

        def start(self, master, symbols):
            self.start_calls += 1
            raise RuntimeError("spot subscription partially registered")

        def stop(self):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise RuntimeError("temporary spot rollback failure")

    spot = PartiallyFailingSpotFeed()
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=spot,
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )

    try:
        runtime.start()
    except RuntimeError as exc:
        assert "cleanup is pending" in str(exc)
    else:
        raise AssertionError("partially failed spot startup must be rejected")

    assert spot.start_calls == 1
    assert spot.stop_calls == 1
    assert runtime.snapshot()["status"] == "STOPPING"

    stopped = runtime.stop()
    assert stopped["status"] == "STOPPED"
    assert spot.stop_calls == 2


def test_runtime_retains_option_feed_when_sampler_start_fails(monkeypatch):
    import pytest
    import app.scanner.iv_skew_flip.runtime as runtime_module

    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    rows = (
        make_rows("NIFTY", "12NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 200, 10)
    )
    created = {}

    class TrackedFeed(FakeOptionFeed):
        def __init__(self, symbol):
            super().__init__(symbol)
            self.stop_calls = 0

        def stop(self):
            self.stop_calls += 1
            super().stop()

    class FailingSampler(FakeSampler):
        def start(self):
            raise RuntimeError("sampler thread failed to start")

    def factory(symbol):
        created[symbol] = TrackedFeed(symbol)
        return created[symbol]

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FailingSampler)
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=FakeSpotFeed(),
        option_feed_factory=factory,
        clock_ns=lambda: 10_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }

    runtime.refresh_once()
    feed = created["NIFTY_WEEKLY"]
    assert feed.started is True
    assert runtime._option_feeds["NIFTY_WEEKLY"] is feed
    assert "NIFTY_WEEKLY" in runtime.snapshot()["option_chains"]
    stopped = runtime.stop()
    assert stopped["status"] == "STOPPED"
    assert feed.started is False
    assert feed.stop_calls >= 1


def test_sampler_start_retry_does_not_resubscribe_unchanged_option_universe(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    rows = (
        make_rows("NIFTY", "12NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 200, 10)
    )
    created = {}
    attempts = {"count": 0}

    class RecoveringSampler(FakeSampler):
        def start(self):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("temporary sampler startup failure")
            self.started = True

    def factory(symbol):
        created[symbol] = FakeOptionFeed(symbol)
        return created[symbol]

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", RecoveringSampler)
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=FakeSpotFeed(),
        option_feed_factory=factory,
        clock_ns=lambda: 10_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }

    runtime.refresh_once()
    feed = created["NIFTY_WEEKLY"]
    assert feed.start_calls == 1
    assert "NIFTY_WEEKLY" in runtime._last_error

    runtime.refresh_once()
    assert feed.start_calls == 1
    assert runtime._samplers["NIFTY_WEEKLY"].started is True
    assert "NIFTY_WEEKLY" not in runtime._last_error


def test_runtime_retains_option_feed_when_subscription_start_partially_fails():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    rows = (
        make_rows("NIFTY", "12NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 200, 10)
    )
    created = {}

    class PartiallyFailingFeed(FakeOptionFeed):
        def __init__(self, symbol):
            super().__init__(symbol)
            self.stop_calls = 0

        def start(self, universe):
            self.universe = universe
            # Simulate the common feed registering this consumer before an
            # exception is raised by a later subscription operation.
            raise RuntimeError("partial option subscription failure")

        def stop(self):
            self.stop_calls += 1
            self.started = False

    def factory(symbol):
        created[symbol] = PartiallyFailingFeed(symbol)
        return created[symbol]

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=FakeSpotFeed(),
        option_feed_factory=factory,
        clock_ns=lambda: 10_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }

    runtime.refresh_once()
    feed = created["NIFTY_WEEKLY"]
    assert runtime._option_feeds["NIFTY_WEEKLY"] is feed
    assert "partial option subscription failure" in runtime._last_error["NIFTY_WEEKLY"]

    stopped = runtime.stop()
    assert stopped["status"] == "STOPPED"
    assert feed.stop_calls >= 1



def test_runtime_rolls_weekly_expiry_forward_after_1530_ist(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    now = datetime(2026, 11, 9, 15, 31, tzinfo=IST)
    rows = (
        make_rows("NIFTY", "09NOV2026", 100, 10)
        + make_rows("NIFTY", "12NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
    )
    created = {}

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=FakeSpotFeed(),
        option_feed_factory=lambda symbol: created.setdefault(
            symbol, FakeOptionFeed(symbol)
        ),
        clock_ns=lambda: 20_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }

    result = runtime.refresh_once()

    assert result["option_chains"]["NIFTY_WEEKLY"]["universe_tokens"] == 22
    assert created["NIFTY_WEEKLY"].universe.expiry == "2026-11-12"
    assert all(
        contract.expiry == "2026-11-12"
        for contract in created["NIFTY_WEEKLY"].universe.contracts
    )



def test_runtime_uses_stale_spot_only_for_universe_then_recovers_on_fresh_tick(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    now = datetime(2026, 11, 10, 8, 30, tzinfo=IST)
    rows = (
        make_rows("NIFTY", "17NOV2026", 100, 10)
        + make_rows("NIFTY", "24NOV2026", 100, 10)
    )
    spot = FakeSpotFeed()
    spot.prices = {}  # exchange closed: no fresh spot, but a persisted last price exists
    created = {}
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=spot,
        option_feed_factory=lambda symbol: created.setdefault(symbol, FakeOptionFeed(symbol)),
        clock_ns=lambda: 20_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }

    closed = runtime.refresh_once()
    assert closed["option_chains"]["NIFTY_WEEKLY"]["universe_tokens"] == 22
    assert created["NIFTY_WEEKLY"].universe.expiry == "2026-11-17"
    assert "USING_LAST_SPOT_FOR_UNIVERSE_ONLY" in closed["option_chains"]["NIFTY_WEEKLY"]["error"]
    assert runtime._samplers["NIFTY_WEEKLY"].snapshot()["status"] == "WAITING"
    assert closed["signals"]["NIFTY_WEEKLY"]["signals_allowed"] is False
    assert closed["early_move"]["NIFTY_WEEKLY"]["signals_allowed"] is False

    # A new market tick becomes fresh; the same refresh loop resumes live sampling.
    spot.prices["NIFTY"] = SpotSnapshot(120.0, 20_000_000_000)
    live = runtime.refresh_once()
    assert live["option_chains"]["NIFTY_WEEKLY"]["universe_tokens"] == 22
    assert runtime._samplers["NIFTY_WEEKLY"].snapshot()["status"] == "LIVE"
    assert created["NIFTY_WEEKLY"].universe.atm_strike == 120.0



def test_production_feed_manager_injection_isolates_all_iv_consumers_on_one_mode_three_socket():
    from app.market_data.common_strategy_feed import CommonStrategyMarketFeed

    class Manager:
        def snapshot(self):
            return {"subscriptions": 0, "socket_groups": 0}

        def close(self):
            self.closed = True

    manager = Manager()
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        feed_manager=manager,
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )

    assert isinstance(runtime.spot_feed.feed, CommonStrategyMarketFeed)
    assert runtime.spot_feed.feed.manager is manager
    assert runtime.spot_feed.feed.mode == 3

    assert isinstance(runtime.future_feed.feed, CommonStrategyMarketFeed)
    assert runtime.future_feed.feed.manager is manager
    assert runtime.future_feed.feed.mode == 3

    option_feed = runtime.option_feed_factory("NIFTY_WEEKLY")
    assert option_feed.feed.manager is manager
    assert option_feed.feed.mode == 3

    equity_feed = runtime.equity_feed_factory()
    assert equity_feed.feed.manager is manager
    assert equity_feed.feed.mode == 3

    stopped = runtime.stop()
    assert stopped["status"] == "STOPPED"
    assert manager.closed is True



def test_weekly_expiry_selection_rolls_after_1530_ist_and_stays_rolled_next_day():
    before_close = datetime(2026, 11, 17, 15, 29, 59, tzinfo=IST)
    at_close = datetime(2026, 11, 17, 15, 30, 0, tzinfo=IST)
    next_day = datetime(2026, 11, 18, 9, 15, tzinfo=IST)

    assert _universe_selection_date(before_close).isoformat() == "2026-11-17"
    assert _universe_selection_date(at_close).isoformat() == "2026-11-18"
    assert _universe_selection_date(next_day).isoformat() == "2026-11-18"



def test_runtime_start_waits_for_lifecycle_transaction_lock():
    from threading import Event, Thread

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    entered = Event()
    finished = Event()
    errors = []

    def start_runtime():
        entered.set()
        try:
            runtime.start(instruments=[])
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    runtime._lifecycle_lock.acquire()
    worker = Thread(target=start_runtime)
    worker.start()
    try:
        assert entered.wait(2)
        assert finished.is_set() is False
    finally:
        runtime._lifecycle_lock.release()
    worker.join(timeout=2)

    assert finished.is_set()
    assert len(errors) == 1
    assert "instrument master is empty" in str(errors[0])


def test_runtime_stop_waits_for_lifecycle_transaction_lock():
    from threading import Event, Thread

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    entered = Event()
    finished = Event()
    results = []

    def stop_runtime():
        entered.set()
        results.append(runtime.stop())
        finished.set()

    runtime._lifecycle_lock.acquire()
    worker = Thread(target=stop_runtime)
    worker.start()
    try:
        assert entered.wait(2)
        assert finished.is_set() is False
    finally:
        runtime._lifecycle_lock.release()
    worker.join(timeout=2)

    assert finished.is_set()
    assert results[0]["status"] == "STOPPED"



def test_runtime_snapshot_requires_nonblank_telegram_credentials(monkeypatch):
    from app.core.config import settings

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "   ")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")
    assert runtime.snapshot()["telegram_configured"] is False

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "   ")
    assert runtime.snapshot()["telegram_configured"] is False

    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")
    assert runtime.snapshot()["telegram_configured"] is True
