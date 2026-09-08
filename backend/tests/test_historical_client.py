import pytest

from app.core.exceptions import TradingAppException
from app.market_data.historical import HistoricalDataClient


class FakeSmartApi:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def getCandleData(self, payload):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeMarketClient:
    def __init__(self, api):
        self.api = api

    def get_client(self):
        return self.api


def test_historical_client_retries_transient_exception(monkeypatch):
    api = FakeSmartApi([RuntimeError("temporary"), {
        "status": True,
        "data": [["2026-01-02T09:15:00", 100, 102, 99, 101, 10]],
    }])
    sleeps = []
    client = HistoricalDataClient(
        FakeMarketClient(api), max_retries=2, backoff_seconds=0.25, sleep_fn=sleeps.append
    )

    result = client.get_candles("NSE", "2885", "ONE_MINUTE", "2026-01-02 09:15", "2026-01-02 09:16")

    assert result["status"] is True
    assert api.calls == 2
    assert sleeps == [0.25]


def test_historical_client_uses_exponential_backoff(monkeypatch):
    api = FakeSmartApi([RuntimeError("one"), RuntimeError("two"), {
        "status": True,
        "data": [],
    }])
    sleeps = []
    client = HistoricalDataClient(
        FakeMarketClient(api), max_retries=2, backoff_seconds=0.5, sleep_fn=sleeps.append
    )

    result = client.get_candles("NSE", "2885", "ONE_MINUTE", "2026-01-02 09:15", "2026-01-02 09:16")
    assert result["status"] is True
    assert api.calls == 3
    assert sleeps == [0.5, 1.0]


def test_historical_client_surfaces_final_provider_failure():
    api = FakeSmartApi([RuntimeError("provider down"), RuntimeError("still down")])
    client = HistoricalDataClient(FakeMarketClient(api), max_retries=1, backoff_seconds=0, sleep_fn=lambda _: None)

    with pytest.raises(TradingAppException) as exc:
        client.get_candles("NSE", "2885", "ONE_MINUTE", "2026-01-02 09:15", "2026-01-02 09:16")

    assert exc.value.code == "HistoricalDataRequestError"
    assert api.calls == 2


def test_historical_client_rejects_wrong_date_format_without_provider_call():
    api = FakeSmartApi([])
    client = HistoricalDataClient(FakeMarketClient(api))

    with pytest.raises(TradingAppException) as exc:
        client.get_candles("NSE", "2885", "ONE_MINUTE", "2026-01-02T09:15", "2026-01-02 09:16")

    assert exc.value.code == "InvalidDateFormat"
    assert api.calls == 0
