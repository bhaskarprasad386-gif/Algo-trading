from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.market_data.shared_index_derived_iv import SharedIndexDerivedIV
from app.scanner.iv_skew_flip.engine import DEFAULT_CONFIGS, OptionQuote, _iv, implied_volatility
from app.scanner.iv_skew_flip.sampler import IVOneSecondSampler, SpotSnapshot


def test_local_iv_solver_is_disabled():
    assert implied_volatility("CE", 26000, 26000, 0.01, 100) is None
    quote = OptionQuote(
        underlying="NIFTY", expiry="2026-12-03", strike=26000,
        option_type="CE", timestamp_ns=1, ltp=100,
    )
    assert _iv(quote, 26000, 0.01, DEFAULT_CONFIGS[0]) is None


def test_shared_market_data_never_decorates_locally_calculated_iv():
    adapter = SharedIndexDerivedIV()
    rows = adapter.decorate(
        [{
            "exchange": "NFO", "segment": "OPTIDX", "token": "123",
            "instrument_type": "option", "fresh": True, "ltp": 100,
            "underlying": "NIFTY", "strike": 26000, "expiry": "2026-12-03",
            "option_type": "CE",
        }],
        {"NIFTY": 26000},
    )
    assert rows[0]["iv_percent"] is None
    assert rows[0]["iv_source"] is None
    assert rows[0]["iv_delta_1s"] is None


def test_one_second_sampler_keeps_oi_but_never_solves_or_displays_iv():
    now = datetime(2026, 11, 27, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    now_ns = 1_000_000_000_000_000_000

    class QuoteSource:
        _universe = SimpleNamespace(expiry="2026-12-03")

        def latest_quotes(self):
            return (OptionQuote(
                underlying="NIFTY", expiry="2026-12-03", strike=26000,
                option_type="CE", timestamp_ns=now_ns, ltp=100,
                bid=99, ask=101, oi=1000, volume=100,
                token="123", oi_timestamp_ns=now_ns,
            ),)

    sampler = IVOneSecondSampler(
        quote_source=QuoteSource(),
        spot_provider=lambda: SpotSnapshot(26000, now_ns),
        config=DEFAULT_CONFIGS[0],
        history_session_factory=None,
        clock_ns=lambda: now_ns,
        clock_datetime=lambda: now,
    )
    snapshot = sampler.sample_once(now_ns=now_ns, now=now, spot=SpotSnapshot(26000, now_ns))
    assert snapshot["live_metrics"][0]["oi"] == 1000
    assert snapshot["live_metrics"][0]["iv"] is None
    assert snapshot["live_metrics"][0]["iv_status"] == "DISABLED"
    assert snapshot["iv_live_count"] == 0
    assert snapshot["iv_calculation"] == "DISABLED"
