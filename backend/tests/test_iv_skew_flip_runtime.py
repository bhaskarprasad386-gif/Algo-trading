from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.scanner.iv_skew_flip.engine import DEFAULT_CONFIGS
from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime
from app.scanner.iv_skew_flip.sampler import SpotSnapshot


IST = ZoneInfo("Asia/Kolkata")


class FakeSpotFeed:
    def __init__(self):
        self.prices = {
            "NIFTY": SpotSnapshot(100.0, 10_000_000_000),
            "BANKNIFTY": SpotSnapshot(200.0, 10_000_000_000),
        }

    def spot_snapshot(self, symbol):
        return self.prices.get(symbol)

    def snapshot(self):
        return {"status": "LIVE", "spot": {k: v.price for k, v in self.prices.items()}}

    def stop(self):
        pass


class FakeOptionFeed:
    def __init__(self, symbol):
        self.symbol = symbol
        self.universe = None
        self.started = False

    def start(self, universe):
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
        + make_rows("BANKNIFTY", "26NOV2026", 200, 100)
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
    runtime._rows_by_symbol = {
        config.symbol: runtime._rows_for_symbol(rows, config.symbol)
        for config in DEFAULT_CONFIGS
    }

    first = runtime.refresh_once()
    assert first["indices"]["NIFTY"]["universe_tokens"] == 14
    assert first["indices"]["BANKNIFTY"]["universe_tokens"] == 14
    assert created["NIFTY"].universe.strikes[3] == 100.0
    assert created["BANKNIFTY"].universe.strikes[3] == 200.0

    # Same 7-strike window must not churn the option subscription.
    first_universe = created["NIFTY"].universe
    runtime.refresh_once()
    assert created["NIFTY"].universe is first_universe

    # A spot move that changes the centered ATM window causes a clean
    # per-index handoff, without touching the other underlying.
    spot.prices["NIFTY"] = SpotSnapshot(140.0, 11_000_000_000)
    runtime.refresh_once()
    assert created["NIFTY"].universe.strikes[3] == 140.0
    assert created["BANKNIFTY"].universe.strikes[3] == 200.0


def test_runtime_waits_for_fresh_spot_before_option_subscriptions(monkeypatch):
    import app.scanner.iv_skew_flip.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "IVOneSecondSampler", FakeSampler)
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    rows = make_rows("NIFTY", "12NOV2026", 100, 10) + make_rows("BANKNIFTY", "26NOV2026", 200, 100)
    spot = FakeSpotFeed()
    spot.prices = {}
    runtime = IVSkewFlipRuntime(
        instrument_master=FakeMaster(rows), spot_feed=spot,
        option_feed_factory=lambda symbol: FakeOptionFeed(symbol),
        clock_ns=lambda: 10_000_000_000, clock_datetime=lambda: now,
    )
    runtime._rows_by_symbol = {
        config.symbol: runtime._rows_for_symbol(rows, config.symbol)
        for config in DEFAULT_CONFIGS
    }
    result = runtime.refresh_once()
    assert result["indices"]["NIFTY"]["universe_tokens"] == 0
    assert result["indices"]["BANKNIFTY"]["universe_tokens"] == 0


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
    runtime._evaluate_symbol = lambda config, when, stamp: evaluated.append((config.symbol, stamp))

    runtime._maybe_evaluate(now, 10_000_000_000)
    runtime._maybe_evaluate(now, 69_000_000_000)
    assert len(evaluated) == 2

    runtime._maybe_evaluate(now, 70_000_000_000)
    assert len(evaluated) == 4


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
    runtime._signals["NIFTY"] = {
        "confluence": {"direction": "BULLISH", "status": "SIGNAL"},
        "signals_allowed": True,
    }

    assert runtime._evaluate_symbol(DEFAULT_CONFIGS[0], now, 20_000_000_000) is True
    result = runtime.snapshot()["signals"]["NIFTY"]
    assert result["signals_allowed"] is False
    assert result["confluence"]["status"] == "WAITING"
    assert result["confluence"]["direction"] is None
    assert all(item["status"] == "WAITING" for item in result["strategies"])
