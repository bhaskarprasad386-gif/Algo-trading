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
    assert result["oi_live_count"] == 2
    assert result["iv_live_count"] == 2
    assert result["history_samples_by_token"] == {"ce-token": 1, "pe-token": 1}
    assert len(result["live_metrics"]) == 2
    assert {row["token"] for row in result["live_metrics"]} == {"ce-token", "pe-token"}
    assert all(row["oi_live"] and row["iv_live"] for row in result["live_metrics"])
    assert all(row["iv_unit"] == "PERCENT" for row in result["live_metrics"])
    assert all(row["iv_input"] == "BID_ASK_MIDPOINT" for row in result["live_metrics"])
    assert all(row["source_age_ms"] == 0.0 for row in result["live_metrics"])

    # A one-second UI refresh may reuse the same still-fresh broker quote,
    # but it must not pretend that OI changed or create duplicate history rows.
    result = sampler.sample_once(now_ns=now_ns + 1_000_000_000, now=now, spot=SpotSnapshot(21950, now_ns + 1_000_000_000))
    assert result["sample_count"] == 2
    assert result["history_samples_by_token"] == {"ce-token": 1, "pe-token": 1}
    assert all(row["oi_change_1s"] is None for row in result["live_metrics"])
    assert all(row["iv_change_1s"] is None for row in result["live_metrics"])
    assert all(row["ltp_change_1s"] is None for row in result["live_metrics"])


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
        "NIFTY", "2026-11-12", 21950, "CE", now_ns + 1_000_000_000,
        premium + 5, 104.5, 105.5, 9400, 12, "ce-token",
    ),)
    result = sampler.sample_once(
        now_ns=now_ns + 1_000_000_000, now=now,
        spot=SpotSnapshot(21950, now_ns + 1_000_000_000),
    )
    assert result["oi_values"]["ce-token"] == 9400
    assert result["history_samples_by_token"]["ce-token"] == 2
    metric = result["live_metrics"][0]
    assert metric["change_interval_seconds"] == 1.0
    assert metric["oi_change_1s"] == -600
    assert metric["oi_change_pct_1s"] == -6.0
    assert metric["ltp_change_1s"] == 5.0
    assert metric["ltp_change_pct_1s"] == 5.0
    assert metric["iv_change_1s"] is not None


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


import pytest


@pytest.mark.parametrize("spot", [
    SpotSnapshot(float("nan"), 1_800_000_000_000_000_000),
    SpotSnapshot(float("inf"), 1_800_000_000_000_000_000),
    SpotSnapshot(float("-inf"), 1_800_000_000_000_000_000),
    SpotSnapshot(True, 1_800_000_000_000_000_000),
    SpotSnapshot(21950.0, True),
    SpotSnapshot(21950.0, 0),
])
def test_sampler_rejects_invalid_spot_snapshot_values(spot):
    source = QuoteSource()
    now_ns = 1_800_000_000_000_000_000
    sampler = _sampler(source, lambda: now_ns)
    result = sampler.sample_once(
        now_ns=now_ns,
        now=datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        spot=spot,
    )
    assert result["status"] == "WAITING"
    assert result["reason"] == "fresh spot snapshot unavailable"


@pytest.mark.parametrize("spot", [
    (True, 1_800_000_000_000_000_000),
    (21950.0, True),
    (21950.0, 1_800_000_000_000_000_000.0),
    (float("nan"), 1_800_000_000_000_000_000),
    (float("inf"), 1_800_000_000_000_000_000),
])
def test_sampler_rejects_invalid_spot_tuple_values(spot):
    source = QuoteSource()
    now_ns = 1_800_000_000_000_000_000
    sampler = _sampler(source, lambda: now_ns)
    result = sampler.sample_once(
        now_ns=now_ns,
        now=datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        spot=spot,
    )
    assert result["status"] == "WAITING"


@pytest.mark.parametrize("oi", [True, -1, 1.5, "1000"])
def test_sampler_does_not_publish_invalid_oi_values(oi):
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", now_ns,
        100.0, 99.5, 100.5, oi, 10, "ce-token",
    ),)
    sampler = _sampler(source, lambda: now_ns)
    result = sampler.sample_once(now_ns=now_ns, now=now, spot=SpotSnapshot(21950, now_ns))
    assert result["oi_values"]["ce-token"] is None
    assert sampler.oi_reference("ce-token") is None


@pytest.mark.parametrize("timestamp_ns", [True, False, 0, -1])
def test_sampler_rejects_invalid_option_source_timestamps(timestamp_ns):
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", timestamp_ns,
        100.0, 99.5, 100.5, 1000, 10, "ce-token",
    ),)
    sampler = _sampler(source, lambda: now_ns)
    result = sampler.sample_once(now_ns=now_ns, now=now, spot=SpotSnapshot(21950, now_ns))
    assert result["sample_count"] == 0
    assert result["status"] == "WAITING"


def test_sampler_clears_previously_live_metrics_when_spot_becomes_stale():
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", now_ns,
        100.0, 99.5, 100.5, 10000, 10, "ce-token",
    ),)
    sampler = _sampler(source, lambda: now_ns)
    live = sampler.sample_once(
        now_ns=now_ns, now=now, spot=SpotSnapshot(21950, now_ns),
    )
    assert live["status"] == "LIVE"
    assert live["sample_count"] == 1
    waiting = sampler.sample_once(
        now_ns=now_ns + 10_000_000_000,
        now=now,
        spot=SpotSnapshot(21950, now_ns),
    )
    assert waiting["status"] == "WAITING"
    assert waiting["sample_count"] == 0
    assert waiting["live_metrics"] == []
    assert waiting["oi_values"] == {}
    assert waiting["iv_values"] == {}
    assert waiting["reason"] == "fresh spot snapshot unavailable"



def test_sampler_inherits_recent_oi_history_only_for_same_selected_contracts():
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    source.quotes = (
        OptionQuote("NIFTY", "2026-11-12", 21950, "CE", now_ns - 310_000_000_000,
                    100.0, 99.5, 100.5, 10000, 10, "shared-ce"),
        OptionQuote("NIFTY", "2026-11-12", 21950, "PE", now_ns - 310_000_000_000,
                    100.0, 99.5, 100.5, 12000, 10, "shared-pe"),
    )
    previous = _sampler(source, lambda: now_ns)
    previous.sample_once(
        now_ns=now_ns - 310_000_000_000, now=now,
        spot=SpotSnapshot(21950, now_ns - 310_000_000_000),
    )
    current = _sampler(QuoteSource(), lambda: now_ns)
    universe = type("Universe", (), {
        "underlying": "NIFTY",
        "expiry": "2026-11-12",
        "contracts": (
            type("Contract", (), {"token": "shared-ce", "strike": 21950.0, "option_type": "CE"})(),
            type("Contract", (), {"token": "new-token", "strike": 22000.0, "option_type": "CE"})(),
        ),
    })()

    copied = current.inherit_history(previous, universe)
    assert copied == 1
    assert current.oi_reference("shared-ce") is not None
    assert current.oi_reference("shared-pe") is None
    assert current.oi_reference("new-token") is None


def test_sampler_does_not_inherit_history_across_expiry():
    now_ns = 1_800_000_000_000_000_000
    source = QuoteSource()
    previous = _sampler(source, lambda: now_ns)
    current = _sampler(QuoteSource(), lambda: now_ns)
    universe = type("Universe", (), {
        "underlying": "NIFTY",
        "expiry": "2026-11-19",
        "contracts": (),
    })()
    assert current.inherit_history(previous, universe) == 0


def test_sampler_persists_source_ticks_and_restores_history_after_restart():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.database import Base
    from app.models.iv_option_observation import IVOptionObservation

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[IVOptionObservation.__table__])
    Session = sessionmaker(bind=engine, autoflush=False)
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    source.quotes = (
        OptionQuote(
            "NIFTY", "2026-11-12", 21950, "CE", now_ns,
            100.0, 99.5, 100.5, 10000, 250, "durable-ce",
        ),
        OptionQuote(
            "NIFTY", "2026-11-12", 22000, "PE", now_ns,
            100.0, 99.5, 100.5, None, 125, "durable-iv-only",
        ),
    )
    first = IVOneSecondSampler(
        quote_source=source,
        spot_provider=lambda: SpotSnapshot(21950, now_ns),
        config=ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
        clock_ns=lambda: now_ns,
        history_session_factory=Session,
    )
    result = first.sample_once(
        now_ns=now_ns, now=now, spot=SpotSnapshot(21950, now_ns),
    )
    assert result["history_persistence"] == "ENABLED"
    assert result["history_persisted_count"] == 2

    # Concurrent refreshes of the same broker tick must remain idempotent.
    from concurrent.futures import ThreadPoolExecutor

    def repeat_refresh(_):
        return first.sample_once(
            now_ns=now_ns + 1_000_000_000, now=now,
            spot=SpotSnapshot(21950, now_ns + 1_000_000_000),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(repeat_refresh, range(8)))
    with Session() as session:
        rows = session.query(IVOptionObservation).all()
        assert len(rows) == 2
        durable = next(row for row in rows if row.token == "durable-ce")
        iv_only = next(row for row in rows if row.token == "durable-iv-only")
        assert durable.volume == 250
        assert durable.oi == 10000
        assert durable.bid == 99.5
        assert durable.ask == 100.5
        assert iv_only.oi is None

    # A fresh sampler instance recovers the source-time history from SQL.
    restarted = IVOneSecondSampler(
        quote_source=QuoteSource(),
        spot_provider=lambda: SpotSnapshot(21950, now_ns + 2_000_000_000),
        config=ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
        clock_ns=lambda: now_ns + 2_000_000_000,
        history_session_factory=Session,
    )
    restored = restarted.sample_once(
        now_ns=now_ns + 2_000_000_000, now=now,
        spot=SpotSnapshot(21950, now_ns + 2_000_000_000),
    )
    assert restored["history_samples_by_token"] == {"durable-ce": 1, "durable-iv-only": 1}
    engine.dispose()


def test_sampler_keeps_live_metrics_and_retries_history_after_storage_failure():
    now_ns = 1_800_000_000_000_000_000
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    source = QuoteSource()
    source.quotes = (OptionQuote(
        "NIFTY", "2026-11-12", 21950, "CE", now_ns,
        100.0, 99.5, 100.5, 10000, 10, "retry-ce",
    ),)
    attempts = []

    def unavailable_storage():
        attempts.append(True)
        raise RuntimeError("database temporarily unavailable")

    sampler = IVOneSecondSampler(
        quote_source=source,
        spot_provider=lambda: SpotSnapshot(21950, now_ns),
        config=ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
        clock_ns=lambda: now_ns,
        history_session_factory=unavailable_storage,
    )
    # Skip restore so this test isolates the write path.
    sampler._history_loaded = True
    first = sampler.sample_once(
        now_ns=now_ns, now=now, spot=SpotSnapshot(21950, now_ns),
    )
    assert first["status"] == "LIVE"
    assert first["sample_count"] == 1
    assert first["history_samples_by_token"] == {"retry-ce": 1}
    assert "database temporarily unavailable" in first["history_persistence_error"]

    second = sampler.sample_once(
        now_ns=now_ns + 1_000_000_000, now=now,
        spot=SpotSnapshot(21950, now_ns + 1_000_000_000),
    )
    assert second["sample_count"] == 1
    assert second["history_samples_by_token"] == {"retry-ce": 1}
    assert len(attempts) == 2
    assert "database temporarily unavailable" in second["history_persistence_error"]
