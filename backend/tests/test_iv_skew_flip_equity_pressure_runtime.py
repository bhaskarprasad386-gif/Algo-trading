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
