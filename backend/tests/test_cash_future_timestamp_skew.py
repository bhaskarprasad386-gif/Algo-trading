from datetime import datetime, timedelta

import pytest

from app.scanner.cash_future_collector import CashFutureHistoryCollector


class FakeMaster:
    instruments = []

    def search(self, exchange=None):
        return self.instruments


class FakeMarketClient:
    pass


def test_quote_timestamp_skew_rejects_asynchronous_quotes():
    collector = CashFutureHistoryCollector(
        ["ABC"], FakeMarketClient(), FakeMaster(), max_quote_timestamp_skew_seconds=5
    )
    cash = datetime(2026, 9, 4, 10, 0, 0).astimezone()
    future = cash + timedelta(seconds=6)

    with pytest.raises(ValueError, match="cash-future quote timestamp skew for ABC30SEP2026FUT"):
        collector._validate_quote_timestamp_skew(cash, future, "ABC30SEP2026FUT")


def test_quote_timestamp_skew_accepts_synchronized_quotes():
    collector = CashFutureHistoryCollector(
        ["ABC"], FakeMarketClient(), FakeMaster(), max_quote_timestamp_skew_seconds=5
    )
    cash = datetime(2026, 9, 4, 10, 0, 0).astimezone()
    future = cash + timedelta(seconds=5)

    collector._validate_quote_timestamp_skew(cash, future, "ABC30SEP2026FUT")


def test_quote_timestamp_skew_ignores_missing_timestamp():
    collector = CashFutureHistoryCollector(
        ["ABC"], FakeMarketClient(), FakeMaster(), max_quote_timestamp_skew_seconds=5
    )
    collector._validate_quote_timestamp_skew(None, datetime.now().astimezone(), "ABC30SEP2026FUT")


def test_quote_timestamp_skew_config_must_be_positive_or_none():
    with pytest.raises(ValueError, match="max_quote_timestamp_skew_seconds must be positive or None"):
        CashFutureHistoryCollector(
            ["ABC"], FakeMarketClient(), FakeMaster(), max_quote_timestamp_skew_seconds=0
        )
