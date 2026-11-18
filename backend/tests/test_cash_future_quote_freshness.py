from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.scanner.cash_future_collector import _full_quote, _quote_timestamp, CashFutureHistoryCollector

IST = ZoneInfo("Asia/Kolkata")


def test_quote_timestamp_parses_epoch_milliseconds():
    value = datetime(2026, 9, 4, 10, 0, tzinfo=IST).timestamp() * 1000
    parsed = _quote_timestamp(value)
    assert parsed is not None
    assert parsed.astimezone(IST).replace(microsecond=0) == datetime(2026, 9, 4, 10, 0, tzinfo=IST)


def test_quote_timestamp_parses_iso_and_naive_as_ist():
    assert _quote_timestamp("2026-09-04T10:00:00+05:30") == datetime(2026, 9, 4, 10, 0, tzinfo=IST)
    assert _quote_timestamp("2026-09-04 10:00:00") == datetime(2026, 9, 4, 10, 0, tzinfo=IST)


def test_full_quote_preserves_supported_quote_timestamp():
    quote = _full_quote({
        "status": True,
        "data": {"fetched": [{"ltp": 100, "tradeVolume": 1, "opnInterest": 2,
            "exchangeTimestamp": "2026-09-04T10:00:00+05:30", "depth": {}}]},
    })
    assert quote["quote_timestamp"] == datetime(2026, 9, 4, 10, 0, tzinfo=IST)


def test_full_quote_leaves_timestamp_missing_when_broker_omits_it():
    quote = _full_quote({"status": True, "data": {"fetched": [{"ltp": 100, "depth": {}}]}})
    assert quote["quote_timestamp"] is None


def test_quote_freshness_rejects_stale_timestamp(monkeypatch):
    collector = CashFutureHistoryCollector([], max_quote_age_seconds=15)
    now = datetime(2026, 9, 4, 10, 0, tzinfo=IST)
    monkeypatch.setattr("app.scanner.cash_future_collector.datetime", type("FixedDateTime", (), {
        "now": staticmethod(lambda tz=None: now),
    }))
    with pytest.raises(ValueError, match="stale CURRENT quote"):
        collector._validate_quote_freshness("CURRENT", "ABC30SEP2026FUT", now - timedelta(seconds=16))


def test_quote_freshness_accepts_fresh_timestamp(monkeypatch):
    collector = CashFutureHistoryCollector([], max_quote_age_seconds=15)
    now = datetime(2026, 9, 4, 10, 0, tzinfo=IST)
    monkeypatch.setattr("app.scanner.cash_future_collector.datetime", type("FixedDateTime", (), {
        "now": staticmethod(lambda tz=None: now),
    }))
    collector._validate_quote_freshness("CURRENT", "ABC30SEP2026FUT", now - timedelta(seconds=5))


def test_quote_freshness_skips_missing_timestamp():
    collector = CashFutureHistoryCollector([], max_quote_age_seconds=15)
    collector._validate_quote_freshness("CURRENT", "ABC30SEP2026FUT", None)


def test_quote_freshness_config_must_be_positive_or_none():
    with pytest.raises(ValueError, match="max_quote_age_seconds must be positive or None"):
        CashFutureHistoryCollector([], max_quote_age_seconds=0)
