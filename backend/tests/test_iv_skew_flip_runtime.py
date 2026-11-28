from datetime import datetime, timedelta
from pathlib import Path

import pytest
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

    def resolved_universe(self):
        return self.universe

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
                "token": f"{symbol}-{expiry}-{int(strike)}-{side}",
                "lotsize": "25",
                "strike": str(int(strike * 100)),
            })
    return rows




def test_broker_snapshot_context_resolves_each_active_underlying_independently():
    now = datetime(2026, 11, 25, 10, 0, tzinfo=IST)
    rows = (
        # A single NIFTY expiry in a month is classified as monthly. Include
        # the next weekly and month-end contracts so the weekly stream resolves.
        make_rows("NIFTY", "03DEC2026", 100, 10)
        + make_rows("NIFTY", "31DEC2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 200, 100)
    )
    spot = FakeSpotFeed()
    # This fixture's BANKNIFTY ladder starts at 100 because negative strikes
    # are invalid; place spot near the middle so ATM +/- 3 is representable.
    spot.prices["BANKNIFTY"] = SpotSnapshot(700.0, 10_000_000_000)
    spot.last_prices["BANKNIFTY"] = spot.prices["BANKNIFTY"]
    created = {}

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=spot,
        option_feed_factory=lambda stream_id: created.setdefault(
            stream_id, FakeOptionFeed(stream_id)
        ),
        clock_ns=lambda: 10_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }
    runtime.refresh_once()

    nifty_universe, nifty_spot = runtime.broker_snapshot_context("NIFTY_WEEKLY")
    assert "BANKNIFTY_MONTHLY" in runtime._option_feeds, (
        f"BANKNIFTY Monthly feed did not initialize: {runtime._last_error!r}"
    )
    bank_universe, bank_spot = runtime.broker_snapshot_context("BANKNIFTY_MONTHLY")

    assert nifty_universe.underlying == "NIFTY"
    assert nifty_universe.expiry == "2026-12-03"
    assert bank_universe.underlying == "BANKNIFTY"
    assert len(nifty_universe.contracts) == 22
    assert len(bank_universe.contracts) == 22
    assert nifty_spot is spot.last_spot_snapshot("NIFTY")
    assert bank_spot is spot.last_spot_snapshot("BANKNIFTY")

    try:
        runtime.broker_snapshot_context("NIFTY_MONTHLY")
    except RuntimeError as exc:
        assert "not initialized" in str(exc)
    else:
        raise AssertionError("disabled NIFTY Monthly must not expose a broker snapshot universe")

    runtime.stop()


def test_runtime_rebuilds_only_changed_strike_universe(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    rows = (
        make_rows("NIFTY", "12NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 900, 100)
    )
    spot = FakeSpotFeed()
    spot.prices["BANKNIFTY"] = SpotSnapshot(900.0, 10_000_000_000)
    spot.last_prices["BANKNIFTY"] = spot.prices["BANKNIFTY"]
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
    assert first["enabled_streams"] == ["NIFTY_WEEKLY", "BANKNIFTY_MONTHLY"]
    assert first["option_chains"]["NIFTY_WEEKLY"]["enabled"] is True
    assert first["option_chains"]["NIFTY_WEEKLY"]["universe_tokens"] == 22
    assert first["option_chains"]["NIFTY_MONTHLY"]["enabled"] is False
    assert first["option_chains"]["NIFTY_MONTHLY"]["option_feed"]["status"] == "DISABLED_BY_SCOPE"
    assert first["option_chains"]["BANKNIFTY_MONTHLY"]["enabled"] is True
    assert first["option_chains"]["BANKNIFTY_MONTHLY"]["universe_tokens"] == 22
    assert first["indices"]["BANKNIFTY"]["universe_tokens"] == 22
    assert set(created) == {"NIFTY_WEEKLY", "BANKNIFTY_MONTHLY"}
    assert created["BANKNIFTY_MONTHLY"].universe.expiry == "2026-11-26"
    assert created["NIFTY_WEEKLY"].universe.expiry == "2026-11-12"

    weekly_universe = created["NIFTY_WEEKLY"].universe
    runtime.refresh_once()
    assert created["NIFTY_WEEKLY"].universe is weekly_universe

    previous_sampler = runtime._samplers["NIFTY_WEEKLY"]
    spot.prices["NIFTY"] = SpotSnapshot(140.0, 11_000_000_000)
    runtime.refresh_once()
    assert created["NIFTY_WEEKLY"].universe.strikes[5] == 140.0
    assert runtime._samplers["NIFTY_WEEKLY"].inherited_from is previous_sampler

    # BANKNIFTY ATM movement independently rotates its seven-strike window;
    # NIFTY's already-resolved weekly universe must remain unchanged.
    weekly_after_move = created["NIFTY_WEEKLY"].universe
    previous_bank_sampler = runtime._samplers["BANKNIFTY_MONTHLY"]
    spot.prices["BANKNIFTY"] = SpotSnapshot(1000.0, 12_000_000_000)
    spot.last_prices["BANKNIFTY"] = spot.prices["BANKNIFTY"]
    runtime.refresh_once()
    assert created["BANKNIFTY_MONTHLY"].universe.atm_strike == 1000.0
    assert len(created["BANKNIFTY_MONTHLY"].universe.contracts) == 22
    assert len(created["BANKNIFTY_MONTHLY"].universe.strikes) == 11
    assert runtime._samplers["BANKNIFTY_MONTHLY"].inherited_from is previous_bank_sampler
    assert created["NIFTY_WEEKLY"].universe is weekly_after_move
    runtime.stop()


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
    assert result["indices"]["BANKNIFTY"]["option_feed"]["status"] == "WAITING_FOR_SPOT"
    assert "WAITING_FOR_SPOT" in result["option_chains"]["NIFTY_WEEKLY"]["error"]
    assert result["option_chains"]["NIFTY_MONTHLY"]["enabled"] is False
    assert result["option_chains"]["BANKNIFTY_MONTHLY"]["enabled"] is True
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
    assert len(evaluated) == 2
    assert {item[0] for item in evaluated} == {"NIFTY_WEEKLY", "BANKNIFTY_MONTHLY"}

    runtime._maybe_evaluate(now, 70_000_000_000)
    assert len(evaluated) == 4
    assert {stream_id for stream_id, _ in evaluated} == {"NIFTY_WEEKLY", "BANKNIFTY_MONTHLY"}


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


def test_runtime_rejects_direct_feed_manager_injection():
    class Manager:
        def close(self):
            raise AssertionError("IV runtime must never close a direct manager")

    with pytest.raises(ValueError, match="SharedIndexMarketDataRunner"):
        IVSkewFlipRuntime(
            instrument_master=FakeMaster([]),
            spot_feed=FakeSpotFeed(),
            feed_manager=Manager(),
            close_feed_manager=False,
            clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
        )


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
        runtime.start(instruments=rows)
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
    assert runtime._last_error["NIFTY_WEEKLY"] == "RuntimeError"

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



def test_runtime_without_shared_runner_uses_only_noop_transports():
    from app.scanner.iv_skew_flip.runtime import _DisabledIVMarketDataTransport

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )

    assert isinstance(runtime.spot_feed.feed, _DisabledIVMarketDataTransport)
    assert runtime.future_feed.snapshot()["status"] == "DISABLED_BY_SCOPE"
    option_feed = runtime.option_feed_factory("NIFTY_WEEKLY")
    assert isinstance(option_feed.feed, _DisabledIVMarketDataTransport)
    equity_feed = runtime.equity_feed_factory()
    assert isinstance(equity_feed.feed, _DisabledIVMarketDataTransport)
    assert runtime.snapshot()["market_data_connection"]["isolation"] == "SHARED_RUNNER_REQUIRED"

    assert runtime.stop()["status"] == "STOPPED"


def test_nifty_weekly_and_banknifty_monthly_keep_tomorrows_expiry_then_roll_after_close(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    now = {"value": datetime(2026, 11, 23, 15, 31, tzinfo=IST)}
    rows = (
        make_rows("NIFTY", "24NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
        + make_rows("NIFTY", "03DEC2026", 100, 10)
        + make_rows("NIFTY", "31DEC2026", 100, 10)
        + make_rows("BANKNIFTY", "24NOV2026", 200, 100)
        + make_rows("BANKNIFTY", "29DEC2026", 200, 100)
    )
    created = {}
    spot = FakeSpotFeed()
    # Keep the synthetic spot inside the positive BANKNIFTY strike ladder.
    spot.prices["BANKNIFTY"] = SpotSnapshot(700.0, 10_000_000_000)
    spot.last_prices["BANKNIFTY"] = spot.prices["BANKNIFTY"]
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=spot,
        option_feed_factory=lambda stream_id: created.setdefault(
            stream_id, FakeOptionFeed(stream_id)
        ),
        clock_ns=lambda: 10_000_000_000,
        clock_datetime=lambda: now["value"],
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }

    # It is already after 15:30 on the prior day; tomorrow's listed expiries
    # must remain selected for both independently configured chains.
    runtime.refresh_once()
    assert created["NIFTY_WEEKLY"].universe.expiry == "2026-11-24"
    assert "BANKNIFTY_MONTHLY" in created, (
        f"BANKNIFTY Monthly feed did not initialize: {runtime._last_error!r}"
    )
    assert created["BANKNIFTY_MONTHLY"].universe.expiry == "2026-11-24"

    # After each actual expiry day closes, both streams move to their next
    # eligible expiry; NIFTY remains weekly while BANKNIFTY remains monthly.
    now["value"] = datetime(2026, 11, 24, 15, 31, tzinfo=IST)
    runtime.refresh_once()
    assert created["NIFTY_WEEKLY"].universe.expiry == "2026-12-03"
    assert created["BANKNIFTY_MONTHLY"].universe.expiry == "2026-12-29"
    runtime.stop()


def test_expiry_selection_does_not_skip_tomorrows_expiry_after_1530():
    before_expiry_day_close = datetime(2026, 11, 16, 15, 31, tzinfo=IST)
    before_expiry_close = datetime(2026, 11, 17, 15, 29, 59, tzinfo=IST)
    at_expiry_close = datetime(2026, 11, 17, 15, 30, 0, tzinfo=IST)
    next_day = datetime(2026, 11, 18, 9, 15, tzinfo=IST)

    # The expiry is tomorrow, so a prior-day post-close refresh must retain it.
    assert _universe_selection_date(
        before_expiry_day_close, ("2026-11-17", "2026-11-24")
    ).isoformat() == "2026-11-16"
    # Keep today's listed expiry until the actual 15:30 IST close.
    assert _universe_selection_date(
        before_expiry_close, ("2026-11-17", "2026-11-24")
    ).isoformat() == "2026-11-17"
    # Once today's expiry has closed, select the next listed expiry.
    assert _universe_selection_date(
        at_expiry_close, ("2026-11-17", "2026-11-24")
    ).isoformat() == "2026-11-18"
    # The next day stays on the new selection date; no second roll occurs.
    assert _universe_selection_date(
        next_day, ("2026-11-24",)
    ).isoformat() == "2026-11-18"


def test_monthly_expiry_selection_rolls_only_after_current_month_expiry_close():
    before_close = datetime(2026, 11, 24, 15, 29, 59, tzinfo=IST)
    after_close = datetime(2026, 11, 24, 15, 30, 0, tzinfo=IST)
    next_day = datetime(2026, 11, 25, 9, 15, tzinfo=IST)

    assert _universe_selection_date(
        before_close, ("2026-11-24", "2026-12-29")
    ).isoformat() == "2026-11-24"
    assert _universe_selection_date(
        after_close, ("2026-11-24", "2026-12-29")
    ).isoformat() == "2026-11-25"
    assert _universe_selection_date(
        next_day, ("2026-12-29",)
    ).isoformat() == "2026-11-25"



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



def test_runtime_page_update_callback_is_best_effort_and_observational():
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_ns=lambda: 20_000_000_000,
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    updates = []
    runtime.set_page_update_callback(updates.append)

    snapshot = runtime.refresh_once()

    assert updates == [snapshot]
    runtime.set_page_update_callback(None)


def test_runtime_attaches_to_shared_index_runner_without_owning_feed_lifecycle():
    from app.scanner.iv_skew_flip.shared_runner_feed import (
        IVSharedRunnerOptionFeedAdapter,
        IVSharedRunnerSpotFeedAdapter,
    )

    class SharedState:
        def records_snapshot(self):
            return []

    class SharedRunner:
        def __init__(self):
            self.state = SharedState()
            self.lifecycle = type("Lifecycle", (), {
                "option_descriptors": lambda _self: (),
            })()
            self.stop_calls = 0

        def snapshot(self):
            return {
                "enabled": True,
                "initial_option_bootstrap_done": True,
                "state": {"instrument_count": 0},
            }

        def stop(self):
            self.stop_calls += 1

    class Manager:
        def __init__(self):
            self.close_calls = 0

        def snapshot(self):
            return {"socket_count": 3}

        def close(self):
            self.close_calls += 1

    shared_runner = SharedRunner()
    manager = Manager()
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        shared_runner=shared_runner,
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )

    assert isinstance(runtime.spot_feed, IVSharedRunnerSpotFeedAdapter)
    option_feed = runtime.option_feed_factory("BANKNIFTY_MONTHLY")
    assert isinstance(option_feed, IVSharedRunnerOptionFeedAdapter)
    snapshot = runtime.snapshot()
    connection = snapshot["market_data_connection"]
    assert connection["isolation"] == "SHARED_INDEX_MARKET_DATA_RUNNER"
    assert connection["iv_owns_subscriptions"] is False
    assert connection["iv_owns_shared_runner"] is False
    assert connection["global_socket_limit"] == 3

    stopped = runtime.stop()
    assert stopped["status"] == "STOPPED"
    assert shared_runner.stop_calls == 0

def test_shared_runner_expiry_selection_rolls_banknifty_monthly_only_after_close():
    from app.market_data.shared_index_market_data_runner import choose_locked_expiry

    rows = (
        make_rows("BANKNIFTY", "26NOV2026", 200, 100)
        + make_rows("BANKNIFTY", "31DEC2026", 200, 100)
    )
    before_close = datetime(2026, 11, 26, 15, 29, 59, tzinfo=IST)
    at_close = datetime(2026, 11, 26, 15, 30, 0, tzinfo=IST)
    prior_day_after_close = datetime(2026, 11, 25, 15, 31, 0, tzinfo=IST)

    assert choose_locked_expiry(rows, "BANKNIFTY", now=prior_day_after_close) == "2026-11-26"
    assert choose_locked_expiry(rows, "BANKNIFTY", now=before_close) == "2026-11-26"
    assert choose_locked_expiry(rows, "BANKNIFTY", now=at_close) == "2026-12-31"

def test_banknifty_telegram_alert_is_identified_as_monthly_pilot(monkeypatch):
    import json
    import app.scanner.iv_skew_flip.runtime as runtime_module
    from app.core.config import settings

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return b'{"ok": true}'

    captured = {}
    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr(runtime_module, "urlopen", fake_urlopen)

    runtime_module.IVSkewFlipRuntime._telegram_sender({
        "underlying": "BANKNIFTY", "strategy": "IV_VOLUME_MOMENTUM",
        "direction": "BULLISH", "option_type": "CE", "strike": 58000,
        "expiry": "2026-11-26", "spot": 58100, "premium": 125,
        "iv_change_points": 0.25, "volume_ratio": 3.0,
        "observed_at_ns": 1_800_000_000_000_000_000, "event_id": 7,
        "reason": "test alert",
    })

    message = captured["body"]["text"]
    assert "BANKNIFTY MONTHLY IV EARLY ENTRY" in message
    assert "PILOT THRESHOLDS" in message
    assert "forward validation pending" in message
    assert "test-chat" == captured["body"]["chat_id"]
    assert captured["timeout"] == 5



def test_broker_snapshot_readiness_waits_for_each_chain_and_spot_independently():
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 25, 10, 0, tzinfo=IST),
    )
    runtime._started = True

    assert runtime.broker_snapshot_ready("NIFTY_WEEKLY") is False
    assert runtime.broker_snapshot_ready("BANKNIFTY_MONTHLY") is False
    assert runtime.broker_snapshot_ready("NIFTY_MONTHLY") is False

    nifty = FakeOptionFeed("NIFTY_WEEKLY")
    nifty.universe = type("Universe", (), {"underlying": "NIFTY"})()
    runtime._option_feeds["NIFTY_WEEKLY"] = nifty
    assert runtime.broker_snapshot_ready("NIFTY_WEEKLY") is True
    assert runtime.broker_snapshot_ready("BANKNIFTY_MONTHLY") is False

    bank = FakeOptionFeed("BANKNIFTY_MONTHLY")
    bank.universe = type("Universe", (), {"underlying": "BANKNIFTY"})()
    runtime._option_feeds["BANKNIFTY_MONTHLY"] = bank
    assert runtime.broker_snapshot_ready("BANKNIFTY_MONTHLY") is True

    runtime._stopping = True
    assert runtime.broker_snapshot_ready("BANKNIFTY_MONTHLY") is False
    runtime._stopping = False
    runtime._started = False
    assert runtime.broker_snapshot_ready("BANKNIFTY_MONTHLY") is False


def test_production_iv_runtime_uses_bounded_three_second_refresh_cadence():
    import ast

    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    constructor = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "IVSkewFlipRuntime"
    )
    refresh = next(
        keyword.value for keyword in constructor.keywords
        if keyword.arg == "refresh_seconds"
    )
    assert isinstance(refresh, ast.Constant) and refresh.value == 3.0

def test_production_shared_runner_mode_uses_read_only_adapters_without_direct_manager():
    from app.scanner.iv_skew_flip.shared_runner_feed import (
        IVSharedRunnerOptionFeedAdapter,
        IVSharedRunnerSpotFeedAdapter,
    )

    class SharedRunner:
        def health_snapshot(self):
            return {"enabled": True, "initial_option_bootstrap_done": True}

    runner = SharedRunner()
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        shared_runner=runner,
        feed_manager=None,
    )

    assert runtime.feed_manager is None
    assert isinstance(runtime.spot_feed, IVSharedRunnerSpotFeedAdapter)
    assert isinstance(
        runtime.option_feed_factory("NIFTY_WEEKLY"),
        IVSharedRunnerOptionFeedAdapter,
    )



def test_shared_runner_mode_never_constructs_an_iv_owned_future_websocket_feed():
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        shared_runner=object(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )

    assert not hasattr(runtime.future_feed, "feed")
    assert runtime.future_feed.snapshot()["status"] == "DISABLED_BY_SCOPE"
    assert runtime.future_feed.snapshot()["owns_subscriptions"] is False
    assert runtime.spot_feed.snapshot()["subscription_owner"] == "shared-index-market-data"


def test_runtime_snapshot_uses_readiness_without_full_shared_runner_health():
    class SharedRunner:
        def readiness_snapshot(self):
            return {"enabled": True, "initial_option_bootstrap_done": True}

        def health_snapshot(self):
            raise AssertionError("runtime status snapshot must not build full runner diagnostics")

    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        shared_runner=SharedRunner(),
    )

    snapshot = runtime.snapshot()

    assert snapshot["market_data_connection"]["runner"] == {
        "enabled": True,
        "initial_option_bootstrap_done": True,
    }
    assert runtime.stop()["status"] == "STOPPED"


def test_runtime_snapshot_builds_each_feed_and_sampler_view_once():
    class CountingSnapshot:
        def __init__(self, payload):
            self.payload = payload
            self.calls = 0

        def snapshot(self):
            self.calls += 1
            return dict(self.payload)

    runtime = IVSkewFlipRuntime(instrument_master=FakeMaster([]))
    feeds = {
        "NIFTY_WEEKLY": CountingSnapshot({"status": "LIVE", "consumer": "nifty"}),
        "BANKNIFTY_MONTHLY": CountingSnapshot({"status": "LIVE", "consumer": "banknifty"}),
    }
    samplers = {
        "NIFTY_WEEKLY": CountingSnapshot({"status": "RUNNING"}),
        "BANKNIFTY_MONTHLY": CountingSnapshot({"status": "RUNNING"}),
    }
    runtime._option_feeds = feeds
    runtime._samplers = samplers

    snapshot = runtime.snapshot()

    assert all(feed.calls == 1 for feed in feeds.values())
    assert all(sampler.calls == 1 for sampler in samplers.values())
    assert snapshot["option_chains"]["NIFTY_WEEKLY"]["option_feed"] == snapshot["indices"]["NIFTY"]["option_feed"]
    assert snapshot["option_chains"]["BANKNIFTY_MONTHLY"]["sampler"] == snapshot["indices"]["BANKNIFTY"]["sampler"]


def test_runtime_withholds_iv_signal_when_any_selected_contract_is_stale():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    now_ns = 10_000_000_000
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_ns=lambda: now_ns,
        clock_datetime=lambda: now,
    )

    class PartialFreshOptionFeed:
        def latest_quotes(self):
            return [object(), object()]

        def snapshot(self, now_ns=None):
            return {
                "status": "PARTIAL",
                "expected_contracts": 3,
                "fresh_contracts": 2,
                "missing_contracts": 1,
            }

    runtime._option_feeds["NIFTY_WEEKLY"] = PartialFreshOptionFeed()
    runtime._samplers["NIFTY_WEEKLY"] = type("Sampler", (), {"snapshot": lambda self: {"status": "WAITING"}})()

    assert runtime._evaluate_symbol(
        "NIFTY_WEEKLY", CHAIN_CONFIGS[0][1], now, now_ns,
    ) is True
    signal = runtime.snapshot()["signals"]["NIFTY_WEEKLY"]
    assert signal["data_ready"] is False
    assert signal["signals_allowed"] is False
    assert signal["confluence"]["status"] == "WAITING"
    assert "2/3 fresh broker-timestamped contracts" in signal["confluence"]["reason"]



def test_iv_on_off_race_stop_cannot_overtake_partial_startup():
    """OFF must wait until the complete ON transaction has unwound/committed."""
    from threading import Event, Thread
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    start_entered = Event()
    release_start = Event()
    stop_entered = Event()
    order = []

    def blocked_start(*, instruments=None):
        order.append("start-enter")
        start_entered.set()
        assert release_start.wait(2.0)
        order.append("start-exit")
        return {"status": "RUNNING"}

    def blocked_stop():
        order.append("stop")
        stop_entered.set()
        return {"status": "STOPPED"}

    runtime._start_locked = blocked_start
    runtime._stop_locked = blocked_stop
    start_thread = Thread(target=lambda: runtime.start(instruments=[]))
    start_thread.start()
    assert start_entered.wait(1.0)

    stop_thread = Thread(target=runtime.stop)
    stop_thread.start()
    assert not stop_entered.wait(0.05), "OFF must not enter during the ON transaction"

    release_start.set()
    start_thread.join(timeout=1.0)
    stop_thread.join(timeout=1.0)
    assert not start_thread.is_alive()
    assert not stop_thread.is_alive()
    assert order == ["start-enter", "start-exit", "stop"]


def test_iv_on_waits_until_off_cleanup_transaction_finishes():
    """A new ON must not clear lifecycle state while OFF still owns cleanup."""
    from threading import Event, Thread
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 6, 10, 0, tzinfo=IST),
    )
    stop_entered = Event()
    release_stop = Event()
    start_entered = Event()
    order = []

    def blocked_stop():
        order.append("stop-enter")
        stop_entered.set()
        assert release_stop.wait(2.0)
        order.append("stop-exit")
        return {"status": "STOPPED"}

    def blocked_start(*, instruments=None):
        order.append("start")
        start_entered.set()
        return {"status": "RUNNING"}

    runtime._stop_locked = blocked_stop
    runtime._start_locked = blocked_start
    stop_thread = Thread(target=runtime.stop)
    stop_thread.start()
    assert stop_entered.wait(1.0)

    start_thread = Thread(target=lambda: runtime.start(instruments=[]))
    start_thread.start()
    assert not start_entered.wait(0.05), "ON must wait for OFF cleanup to finish"

    release_stop.set()
    stop_thread.join(timeout=1.0)
    start_thread.join(timeout=1.0)
    assert not stop_thread.is_alive()
    assert not start_thread.is_alive()
    assert order == ["stop-enter", "stop-exit", "start"]


def test_runtime_selects_current_nifty_weekly_expiry_before_close_and_rolls_after_close(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    rows = (
        make_rows("NIFTY", "09NOV2026", 100, 10)
        + make_rows("NIFTY", "12NOV2026", 100, 10)
        + make_rows("NIFTY", "26NOV2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 900, 100)
    )
    spot = FakeSpotFeed()
    spot.prices["BANKNIFTY"] = SpotSnapshot(900.0, 10_000_000_000)
    spot.last_prices["BANKNIFTY"] = spot.prices["BANKNIFTY"]
    created = {}

    def build_runtime(now):
        runtime = IVSkewFlipRuntime(
            instrument_master=FakeMaster(rows),
            spot_feed=spot,
            option_feed_factory=lambda stream_id: created.setdefault(
                stream_id, FakeOptionFeed(stream_id)
            ),
            clock_ns=lambda: 10_000_000_000,
            clock_datetime=lambda: now,
        )
        runtime._rows_by_stream = {
            stream_id: runtime._rows_for_symbol(rows, config.symbol)
            for stream_id, config in CHAIN_CONFIGS
        }
        return runtime

    before_close = build_runtime(datetime(2026, 11, 9, 15, 29, tzinfo=IST))
    selected = before_close.refresh_once()
    assert created["NIFTY_WEEKLY"].universe.expiry == "2026-11-09"
    assert selected["option_chains"]["NIFTY_WEEKLY"]["expiry_selection"]["selector"] == "SHARED_RUNNER_AUTHORITATIVE"
    before_close.stop()

    created.clear()
    after_close = build_runtime(datetime(2026, 11, 9, 15, 31, tzinfo=IST))
    rolled = after_close.refresh_once()
    assert created["NIFTY_WEEKLY"].universe.expiry == "2026-11-12"
    assert rolled["option_chains"]["NIFTY_WEEKLY"]["expiry_selection"]["selected_expiry"] == "2026-11-12"
    after_close.stop()


def test_runtime_banknifty_monthly_rolls_only_to_next_listed_monthly_expiry(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    now = datetime(2026, 11, 26, 15, 31, tzinfo=IST)
    rows = (
        make_rows("NIFTY", "03DEC2026", 100, 10)
        + make_rows("NIFTY", "31DEC2026", 100, 10)
        + make_rows("BANKNIFTY", "26NOV2026", 900, 100)
        + make_rows("BANKNIFTY", "31DEC2026", 900, 100)
    )
    spot = FakeSpotFeed()
    spot.prices["BANKNIFTY"] = SpotSnapshot(900.0, 10_000_000_000)
    spot.last_prices["BANKNIFTY"] = spot.prices["BANKNIFTY"]
    created = {}
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows),
        spot_feed=spot,
        option_feed_factory=lambda stream_id: created.setdefault(stream_id, FakeOptionFeed(stream_id)),
        clock_ns=lambda: 10_000_000_000,
        clock_datetime=lambda: now,
    )
    runtime._rows_by_stream = {
        stream_id: runtime._rows_for_symbol(rows, config.symbol)
        for stream_id, config in CHAIN_CONFIGS
    }

    result = runtime.refresh_once()
    assert created["BANKNIFTY_MONTHLY"].universe.expiry == "2026-12-31"
    assert result["option_chains"]["BANKNIFTY_MONTHLY"]["expiry_selection"]["selected_expiry"] == "2026-12-31"
    runtime.stop()

def test_early_move_withholds_alerts_when_selected_option_coverage_is_partial():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    now_ns = 10_000_000_000
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster([]),
        spot_feed=FakeSpotFeed(),
        clock_ns=lambda: now_ns,
        clock_datetime=lambda: now,
    )

    class PartialOptionFeed:
        def latest_quotes(self):
            return [object()] * 21

        def snapshot(self, now_ns=None):
            return {
                "status": "PARTIAL",
                "expected_contracts": 22,
                "fresh_contracts": 21,
                "missing_contracts": 1,
            }

        def resolved_universe(self):
            return type("Universe", (), {"strike_step": 50.0})()

    class CountingSampler:
        configured_expiry = "2026-11-12"

        def __init__(self):
            self.snapshot_calls = 0

        def snapshot(self):
            self.snapshot_calls += 1
            return {"raw_live_metrics": []}

    class CountingDetector:
        def __init__(self):
            self.calls = 0

        def update(self, **kwargs):
            self.calls += 1
            return {"state": "CONFIRMED", "signals_allowed": True}

    class CountingAlerts:
        def __init__(self):
            self.calls = 0

        def evaluate(self, **kwargs):
            self.calls += 1

        def snapshot(self, _underlying="NIFTY"):
            return {}

        def snapshot_all(self):
            return {}

    feed = PartialOptionFeed()
    sampler = CountingSampler()
    detector = CountingDetector()
    alerts = CountingAlerts()
    runtime._option_feeds["NIFTY_WEEKLY"] = feed
    runtime._samplers["NIFTY_WEEKLY"] = sampler
    runtime._early_move_detectors["NIFTY_WEEKLY"] = detector
    runtime._early_entry_alerts = alerts

    runtime._evaluate_early_move(now_ns)

    result = runtime.snapshot()["early_move"]["NIFTY_WEEKLY"]
    assert result["state"] == "WATCH"
    assert result["signals_allowed"] is False
    assert result["baseline_status"] == "WAITING_FOR_COMPLETE_OPTION_UNIVERSE"
    assert "21/22" in result["reason"]
    assert detector.calls == 0
    assert alerts.calls == 0
    assert sampler.snapshot_calls == 0
    runtime.stop()
