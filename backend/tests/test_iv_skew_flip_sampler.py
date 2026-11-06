from datetime import datetime
from zoneinfo import ZoneInfo

from app.scanner.iv_skew_flip.engine import OptionQuote, ScannerConfig, black_scholes_price
from app.scanner.iv_skew_flip.sampler import IVOneSecondSampler, SpotSnapshot


class QuoteSource:
    def __init__(self):
        self._universe = type("Universe", (), {"expiry": "2026-11-12"})()
        self.quotes = ()

    def latest_quotes(self):
        return self.quotes


def _sampler(source, clock_ns):
    return IVOneSecondSampler(
        quote_source=source,
        spot_provider=lambda: SpotSnapshot(21950.0, clock_ns()),
        config=ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
        clock_ns=clock_ns,
    )


def test_one_second_sampler_calculates_iv_and_samples_oi_from_fresh_quotes():
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    years = (datetime(2026, 11, 12, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata")) - now).total_seconds() / (365 * 24 * 60 * 60)
    rows = []
    for token, strike, side, oi in (
        ("ce-token", 21950.0, "CE", 12000),
        ("pe-token", 21950.0, "PE", 15000),
    ):
        premium = black_scholes_price(side, 21950.0, strike, years, 0.20)
        rows.append(OptionQuote(
            "NIFTY", "2026-11-12", strike, side, now_ns,
            premium, premium - 0.5, premium + 0.5, oi, 10, token,
        ))
    source.quotes = tuple(rows)
    sampler = _sampler(source, lambda: now_ns)
    result = sampler.sample_once(now_ns=now_ns, now=now, spot=SpotSnapshot(21950, now_ns))
    assert result["cadence_seconds"] == 1
    assert result["status"] == "LIVE"
    assert result["sample_count"] == 2
    assert all(value is not None and 15 < value < 25 for value in result["iv_values"].values())
    assert result["oi_values"] == {"ce-token": 12000, "pe-token": 15000}
    assert result["history_samples_by_token"] == {"ce-token": 1, "pe-token": 1}

    # A one-second UI refresh may reuse the same still-fresh broker quote,
    # but it must not pretend that OI changed or create duplicate history rows.
    result = sampler.sample_once(now_ns=now_ns + 1_000_000_000, now=now, spot=SpotSnapshot(21950, now_ns + 1_000_000_000))
    assert result["sample_count"] == 2
    assert result["history_samples_by_token"] == {"ce-token": 1, "pe-token": 1}


def test_one_second_sampler_records_new_oi_only_on_new_source_tick():
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    premium = 100.0
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", now_ns,
        premium, 99.5, 100.5, 10000, 10, "ce-token",
    ),)
    sampler = _sampler(source, lambda: now_ns + 1_000_000_000)
    sampler.sample_once(now_ns=now_ns, now=now, spot=SpotSnapshot(21950, now_ns))
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", now_ns + 2_000_000_000,
        premium + 5, 104.5, 105.5, 9400, 12, "ce-token",
    ),)
    result = sampler.sample_once(
        now_ns=now_ns + 2_000_000_000, now=now,
        spot=SpotSnapshot(21950, now_ns + 2_000_000_000),
    )
    assert result["oi_values"]["ce-token"] == 9400
    assert result["history_samples_by_token"]["ce-token"] == 2


def test_one_second_sampler_fails_closed_without_fresh_spot():
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    sampler = IVOneSecondSampler(
        quote_source=source,
        spot_provider=lambda: SpotSnapshot(21950, now_ns - 10_000_000_000),
        config=ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
        clock_ns=lambda: now_ns,
    )
    result = sampler.sample_once(now_ns=now_ns, now=now)
    assert result["status"] == "WAITING"
    assert result["sample_count"] == 0
    assert result["reason"] == "fresh spot snapshot unavailable"
