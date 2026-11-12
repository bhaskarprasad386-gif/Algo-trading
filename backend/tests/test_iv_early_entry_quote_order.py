from datetime import datetime
from zoneinfo import ZoneInfo

from app.scanner.iv_skew_flip.engine import OptionQuote, ScannerConfig
from app.scanner.iv_skew_flip.sampler import IVOneSecondSampler, SpotSnapshot


class QuoteSource:
    def __init__(self):
        self._universe = type("Universe", (), {"expiry": "2026-11-12"})()
        self.quotes = ()

    def latest_quotes(self):
        return self.quotes


def test_sampler_rejects_out_of_order_quote_even_when_still_inside_freshness_window():
    base_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", base_ns,
        100.0, 99.5, 100.5, 10000, 10, "ce-token",
    ),)
    sampler = IVOneSecondSampler(
        quote_source=source,
        spot_provider=lambda: SpotSnapshot(21950.0, base_ns + 1_000_000_000),
        config=ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
        clock_ns=lambda: base_ns + 1_000_000_000,
    )
    first = sampler.sample_once(
        now_ns=base_ns, now=now, spot=SpotSnapshot(21950.0, base_ns),
    )
    assert first["sample_count"] == 1
    first_iv = first["live_samples"][0].iv
    assert first_iv is not None

    # This frame is only 1.5 seconds old, so freshness alone would accept it,
    # but its source timestamp is older than the already accepted tick.
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", base_ns - 500_000_000,
        80.0, 79.5, 80.5, 9000, 9, "ce-token",
    ),)
    second_ns = base_ns + 1_000_000_000
    second = sampler.sample_once(
        now_ns=second_ns, now=now,
        spot=SpotSnapshot(21950.0, second_ns),
    )

    assert second["sample_count"] == 0
    assert second["live_samples"] == ()
    assert second["status"] == "WAITING"
    # UI continuity remains allowed, but the delayed frame cannot become LIVE.
    assert second["samples"][0].iv == first_iv
