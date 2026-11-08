from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.scanner.iv_skew_flip.equity_feed import WeightedEquity
from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime


IST = ZoneInfo("Asia/Kolkata")


class FakeSpotFeed:
    def snapshot(self):
        return {"status": "STOPPED"}

    def stop(self):
        pass


class FakeMaster:
    pass


class FakeEquityFeed:
    def __init__(self, snapshot):
        self.payload = snapshot
        self.stopped = False

    def snapshot(self, *, now_ns=None):
        return self.payload

    def stop(self):
        self.stopped = True


def make_runtime(**kwargs):
    return IVSkewFlipRuntime(
        instrument_master=FakeMaster(),
        spot_feed=FakeSpotFeed(),
        clock_ns=lambda: 40_000_000_000,
        clock_datetime=lambda: datetime(2026, 11, 7, 10, 0, tzinfo=IST),
        **kwargs,
    )


def test_equity_pressure_is_disabled_by_default_and_does_not_create_feed():
    runtime = make_runtime()
    assert runtime.equity_pressure_enabled is False
    assert runtime._equity_feed is None
    runtime._refresh_equity_pressure(40_000_000_000)
    assert runtime.snapshot()["equity_pressure"]["enabled"] is False
    assert runtime.snapshot()["equity_pressure"]["signals_allowed"] is False


def test_equity_pressure_cannot_be_enabled_without_injected_verified_basket():
    with pytest.raises(ValueError, match="verified weight basket"):
        make_runtime(equity_pressure_enabled=True)


def test_equity_pressure_uses_source_timestamped_quotes_and_fails_closed_until_windows_ready():
    basket = (WeightedEquity("AAA", 60.0), WeightedEquity("BBB", 40.0))
    runtime = make_runtime(equity_pressure_enabled=True, equity_basket=basket)
    runtime._equity_feed = FakeEquityFeed({
        "status": "LIVE",
        "consumer": "iv-skew-flip-equities",
        "signals_allowed": True,
        "expected_symbols": ["AAA", "BBB"],
        "fresh_symbols": ["AAA", "BBB"],
        "missing_symbols": [],
        "quotes": {
            "AAA": {"timestamp_ns": 40_000_000_000, "ltp": 101.0},
            "BBB": {"timestamp_ns": 40_000_000_000, "ltp": 201.0},
        },
    })

    runtime._refresh_equity_pressure(40_000_000_000)
    result = runtime.snapshot()["equity_pressure"]
    assert result["enabled"] is True
    assert result["feed"]["status"] == "LIVE"
    assert result["status"] == "PARTIAL"
    assert result["signals_allowed"] is False
    assert result["direction"] == "WAITING"


def test_partial_equity_feed_never_allows_pressure_signal():
    basket = (WeightedEquity("AAA", 60.0), WeightedEquity("BBB", 40.0))
    runtime = make_runtime(equity_pressure_enabled=True, equity_basket=basket)
    runtime._equity_feed = FakeEquityFeed({
        "status": "PARTIAL",
        "consumer": "iv-skew-flip-equities",
        "signals_allowed": False,
        "expected_symbols": ["AAA", "BBB"],
        "fresh_symbols": ["AAA"],
        "missing_symbols": ["BBB"],
        "quotes": {"AAA": {"timestamp_ns": 40_000_000_000, "ltp": 101.0}},
    })

    runtime._refresh_equity_pressure(40_000_000_000)
    result = runtime.snapshot()["equity_pressure"]
    assert result["feed"]["status"] == "PARTIAL"
    assert result["signals_allowed"] is False



def test_stop_stops_only_the_owned_equity_feed_and_disables_pressure():
    basket = (WeightedEquity("AAA", 60.0), WeightedEquity("BBB", 40.0))
    runtime = make_runtime(equity_pressure_enabled=True, equity_basket=basket)
    equity_feed = FakeEquityFeed({"status": "LIVE", "signals_allowed": False, "quotes": {}})
    runtime._equity_feed = equity_feed

    result = runtime.stop()

    assert equity_feed.stopped is True
    assert runtime._equity_feed is None
    assert result["equity_pressure"]["status"] == "STOPPED"
    assert result["equity_pressure"]["signals_allowed"] is False
    assert result["equity_pressure"]["enabled"] is True


def test_disabled_runtime_stop_does_not_construct_or_stop_equity_feed():
    factory_calls = []
    runtime = make_runtime(
        equity_pressure_enabled=False,
        equity_feed_factory=lambda: factory_calls.append("created"),
    )

    result = runtime.stop()

    assert factory_calls == []
    assert runtime._equity_feed is None
    assert result["equity_pressure"]["status"] == "DISABLED"
    assert result["equity_pressure"]["signals_allowed"] is False



def test_dual_index_pressure_uses_independent_baskets_feeds_and_snapshots():
    nifty = (WeightedEquity("AAA", 60.0), WeightedEquity("BBB", 40.0))
    bank = (WeightedEquity("CCC", 70.0), WeightedEquity("DDD", 30.0))
    runtime = make_runtime(
        equity_pressure_enabled=True,
        equity_baskets={"NIFTY": nifty, "BANKNIFTY": bank},
    )
    feeds = {
        "NIFTY": FakeEquityFeed({
            "status": "LIVE", "consumer": "iv-skew-flip-equities-nifty",
            "signals_allowed": True, "expected_symbols": ["AAA", "BBB"],
            "fresh_symbols": ["AAA", "BBB"], "missing_symbols": [],
            "quotes": {
                "AAA": {"timestamp_ns": 40_000_000_000, "ltp": 101.0},
                "BBB": {"timestamp_ns": 40_000_000_000, "ltp": 201.0},
            },
        }),
        "BANKNIFTY": FakeEquityFeed({
            "status": "PARTIAL", "consumer": "iv-skew-flip-equities-banknifty",
            "signals_allowed": False, "expected_symbols": ["CCC", "DDD"],
            "fresh_symbols": ["CCC"], "missing_symbols": ["DDD"],
            "quotes": {"CCC": {"timestamp_ns": 40_000_000_000, "ltp": 301.0}},
        }),
    }
    runtime._equity_feeds = feeds

    runtime._refresh_equity_pressure(40_000_000_000)
    result = runtime.snapshot()["equity_pressure_by_underlying"]

    assert set(result) == {"NIFTY", "BANKNIFTY"}
    assert result["NIFTY"]["feed"]["consumer"] == "iv-skew-flip-equities-nifty"
    assert result["BANKNIFTY"]["feed"]["consumer"] == "iv-skew-flip-equities-banknifty"
    assert result["NIFTY"]["feed"]["status"] == "LIVE"
    assert result["BANKNIFTY"]["feed"]["status"] == "PARTIAL"
    assert result["BANKNIFTY"]["signals_allowed"] is False
    assert result["NIFTY"]["signals_allowed"] is False  # warm-up windows are not ready
    assert runtime.snapshot()["equity_pressure"]["enabled"] is True


def test_single_nifty_pressure_basket_is_supported_without_banknifty():
    nifty = (WeightedEquity("AAA", 70.0), WeightedEquity("BBB", 30.0))
    runtime = make_runtime(equity_pressure_enabled=True, equity_baskets={"NIFTY": nifty})
    assert set(runtime._equity_pressures) == {"NIFTY"}


def test_dual_index_pressure_stop_stops_both_owned_feeds():
    nifty = (WeightedEquity("AAA", 100.0),)
    bank = (WeightedEquity("BBB", 100.0),)
    runtime = make_runtime(
        equity_pressure_enabled=True,
        equity_baskets={"NIFTY": nifty, "BANKNIFTY": bank},
    )
    feeds = {
        "NIFTY": FakeEquityFeed({"status": "LIVE", "signals_allowed": False, "quotes": {}}),
        "BANKNIFTY": FakeEquityFeed({"status": "LIVE", "signals_allowed": False, "quotes": {}}),
    }
    runtime._equity_feeds = feeds

    result = runtime.stop()

    assert all(feed.stopped for feed in feeds.values())
    assert runtime._equity_feeds == {}
    assert set(result["equity_pressure_by_underlying"]) == {"NIFTY", "BANKNIFTY"}
    assert all(not item["signals_allowed"] for item in result["equity_pressure_by_underlying"].values())



def test_enabled_dual_index_early_move_waits_for_fresh_pressure_confirmation():
    nifty = (WeightedEquity("AAA", 100.0),)
    bank = (WeightedEquity("BBB", 100.0),)
    runtime = make_runtime(
        equity_pressure_enabled=True,
        equity_baskets={"NIFTY": nifty, "BANKNIFTY": bank},
    )
    option_result = {
        "state": "PRE_MOVE", "direction": "BULLISH", "signals_allowed": True,
        "reason": "option pressure leads spot",
    }

    blocked = runtime._apply_equity_pressure_confirmation("NIFTY", option_result)
    assert blocked["state"] == "WATCH"
    assert blocked["signals_allowed"] is False
    assert blocked["option_signal_state"] == "PRE_MOVE"

    runtime._equity_pressure_by_underlying["NIFTY"] = {
        "status": "LIVE", "direction": "BULLISH", "signals_allowed": True,
    }
    confirmed = runtime._apply_equity_pressure_confirmation("NIFTY", option_result)
    assert confirmed["state"] == "PRE_MOVE"
    assert confirmed["signals_allowed"] is True
    assert confirmed["constituent_pressure_confirmed"] is True


def test_enabled_dual_index_early_move_invalidates_opposing_pressure():
    nifty = (WeightedEquity("AAA", 100.0),)
    bank = (WeightedEquity("BBB", 100.0),)
    runtime = make_runtime(
        equity_pressure_enabled=True,
        equity_baskets={"NIFTY": nifty, "BANKNIFTY": bank},
    )
    runtime._equity_pressure_by_underlying["BANKNIFTY"] = {
        "status": "LIVE", "direction": "BEARISH", "signals_allowed": True,
    }

    result = runtime._apply_equity_pressure_confirmation("BANKNIFTY", {
        "state": "CONFIRMED", "direction": "BULLISH", "signals_allowed": True,
        "reason": "option pressure",
    })

    assert result["state"] == "INVALIDATED"
    assert result["signals_allowed"] is False
    assert "conflicts" in result["reason"]


def test_disabled_dual_index_pressure_does_not_change_legacy_early_move():
    runtime = make_runtime()
    original = {
        "state": "PRE_MOVE", "direction": "BULLISH", "signals_allowed": True,
        "reason": "option pressure leads spot",
    }

    assert runtime._apply_equity_pressure_confirmation("NIFTY", original) == original


def test_dual_index_pressure_keeps_nifty_and_banknifty_feeds_and_snapshots_isolated():
    baskets = {
        "NIFTY": (WeightedEquity("AAA", 60.0), WeightedEquity("BBB", 40.0)),
        "BANKNIFTY": (WeightedEquity("CCC", 70.0), WeightedEquity("DDD", 30.0)),
    }

    class PerIndexFeed(FakeEquityFeed):
        def __init__(self, underlying):
            self.stopped = False
            self.payload = {
                "status": "LIVE",
                "consumer": f"iv-skew-flip-equities-{underlying.lower()}",
                "signals_allowed": True,
                "expected_symbols": [item.symbol for item in baskets[underlying]],
                "fresh_symbols": [item.symbol for item in baskets[underlying]],
                "missing_symbols": [],
                "quotes": {
                    item.symbol: {"timestamp_ns": 40_000_000_000, "ltp": 100.0 + index}
                    for index, item in enumerate(baskets[underlying])
                },
            }

    runtime = make_runtime(
        equity_pressure_enabled=True,
        equity_baskets=baskets,
        equity_feed_factory_by_underlying=PerIndexFeed,
    )
    runtime._equity_feeds = {key: PerIndexFeed(key) for key in baskets}
    runtime._refresh_equity_pressure(40_000_000_000)
    result = runtime.snapshot()["equity_pressure_by_underlying"]

    assert set(result) == {"NIFTY", "BANKNIFTY"}
    assert result["NIFTY"]["feed"]["consumer"] == "iv-skew-flip-equities-nifty"
    assert result["BANKNIFTY"]["feed"]["consumer"] == "iv-skew-flip-equities-banknifty"
    assert result["NIFTY"]["feed"]["expected_symbols"] == ["AAA", "BBB"]
    assert result["BANKNIFTY"]["feed"]["expected_symbols"] == ["CCC", "DDD"]
    assert result["NIFTY"]["signals_allowed"] is False
    assert result["BANKNIFTY"]["signals_allowed"] is False


def test_dual_index_pressure_rejects_partial_or_malformed_basket_configuration():
    with pytest.raises(ValueError, match="only NIFTY and BANKNIFTY"):
        make_runtime(
            equity_pressure_enabled=True,
            equity_baskets={"SENSEX": (WeightedEquity("AAA", 100.0),)},
        )
    with pytest.raises(ValueError, match="non-empty"):
        make_runtime(
            equity_pressure_enabled=True,
            equity_baskets={
                "NIFTY": (WeightedEquity("AAA", 100.0),),
                "BANKNIFTY": (),
            },
        )


def test_one_index_pressure_feed_error_does_not_abort_other_index_snapshot():
    baskets = {
        "NIFTY": (WeightedEquity("AAA", 100.0),),
        "BANKNIFTY": (WeightedEquity("BBB", 100.0),),
    }

    class BrokenFeed:
        consumer = "iv-skew-flip-equities-nifty"

        def snapshot(self, *, now_ns=None):
            raise RuntimeError("temporary NIFTY feed failure")

        def stop(self):
            pass

    bank_feed = FakeEquityFeed({
        "status": "PARTIAL",
        "consumer": "iv-skew-flip-equities-banknifty",
        "signals_allowed": False,
        "expected_symbols": ["BBB"],
        "fresh_symbols": [],
        "missing_symbols": ["BBB"],
        "quotes": {},
    })
    runtime = make_runtime(equity_pressure_enabled=True, equity_baskets=baskets)
    runtime._equity_feeds = {"NIFTY": BrokenFeed(), "BANKNIFTY": bank_feed}

    runtime._refresh_equity_pressure(40_000_000_000)
    result = runtime.snapshot()["equity_pressure_by_underlying"]

    assert result["NIFTY"]["status"] == "ERROR"
    assert result["NIFTY"]["signals_allowed"] is False
    assert "temporary NIFTY feed failure" in result["NIFTY"]["error"]
    assert result["BANKNIFTY"]["feed"]["status"] == "PARTIAL"
    assert result["BANKNIFTY"]["signals_allowed"] is False


def test_dual_index_start_rolls_back_current_and_previous_feeds_on_partial_start_failure(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    rows = [
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": "12NOV2026"},
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": "26NOV2026"},
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "BANKNIFTY", "expiry": "26NOV2026"},
    ]

    class Master:
        def resolve_index_instrument(self, symbol, exchange):
            return {"token": "1", "symbol": symbol}

    class TrackedFeed:
        def __init__(self, underlying, fail=False):
            self.underlying = underlying
            self.fail = fail
            self.stopped = False

        def start(self, master, basket):
            if self.fail:
                raise RuntimeError("partial subscription failure")

        def stop(self):
            self.stopped = True

    created = {}

    def factory(underlying):
        created[underlying] = TrackedFeed(underlying, fail=underlying == "BANKNIFTY")
        return created[underlying]

    monkeypatch.setattr(runtime_module, "download_iv_instruments", lambda master: rows)
    runtime = IVSkewFlipRuntime(
        instrument_master=Master(),
        spot_feed=FakeSpotFeed(),
        clock_datetime=lambda: datetime(2026, 11, 7, 10, 0, tzinfo=IST),
        equity_pressure_enabled=True,
        equity_baskets={
            "NIFTY": (WeightedEquity("AAA", 100.0),),
            "BANKNIFTY": (WeightedEquity("BBB", 100.0),),
        },
        equity_feed_factory_by_underlying=factory,
    )

    with pytest.raises(RuntimeError, match="partial subscription failure"):
        runtime.start()

    assert created["NIFTY"].stopped is True
    assert created["BANKNIFTY"].stopped is True
    assert runtime._equity_feeds == {}
    assert runtime.snapshot()["status"] == "STOPPED"
