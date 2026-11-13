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


def test_early_move_metrics_do_not_reuse_display_carried_iv_for_new_quote():
    base_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", base_ns,
        100.0, 99.5, 100.5, 10000, 10, "ce-token",
    ),)
    sampler = IVOneSecondSampler(
        quote_source=source,
        spot_provider=lambda: SpotSnapshot(21950.0, base_ns),
        config=ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
        clock_ns=lambda: base_ns,
    )
    first = sampler.sample_once(
        now_ns=base_ns, now=now, spot=SpotSnapshot(21950.0, base_ns),
    )
    last_iv = first["live_samples"][0].iv
    assert last_iv is not None

    next_ns = base_ns + 1_000_000_000
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", next_ns,
        None, None, None, 10000, 10, "ce-token",
    ),)
    second = sampler.sample_once(
        now_ns=next_ns, now=now, spot=SpotSnapshot(21950.0, next_ns),
    )

    # UI display is allowed to carry forward the last good IV.
    assert second["live_metrics"][0]["iv"] == last_iv
    assert second["live_metrics"][0]["iv_live"] is False
    # Alert-facing metrics must retain the raw current quote's missing IV.
    assert second["raw_live_metrics"][0]["iv"] is None
    assert second["raw_live_metrics"][0]["iv_input"] is None
    assert second["raw_live_metrics"][0]["source_timestamp_ns"] == next_ns
