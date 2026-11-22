from app.market_data.instruments import InstrumentMaster
from app.core.exceptions import TradingAppException


class FakeResponse:
    status_code = 429
    headers = {"Retry-After": "120"}


class RateLimitedError(Exception):
    response = FakeResponse()


def test_master_download_429_suppresses_alternate_and_cools_down(monkeypatch):
    cls = InstrumentMaster
    monkeypatch.setattr(cls, "_cached_instruments", None)
    monkeypatch.setattr(cls, "_cached_snapshot_date", None)
    monkeypatch.setattr(cls, "_last_download_error", None)
    monkeypatch.setattr(cls, "_cached_disk_snapshot_fetched_at", None)
    monkeypatch.setattr(cls, "_retry_after_seconds", 60.0)
    monkeypatch.setattr(cls, "_last_download_attempt_at", 0.0)
    calls = []

    monkeypatch.setattr(cls, "_read_disk_snapshot", classmethod(lambda _cls: None))
    def fake_download(url, **kwargs):
        calls.append(url)
        raise RateLimitedError("HTTP 429")
    monkeypatch.setattr("app.market_data.instruments.download_master_rows", fake_download)

    manager = cls()
    try:
        manager.download()
    except TradingAppException:
        pass
    else:
        raise AssertionError("429 should fail closed without stale master")

    assert calls == [cls.MASTER_URL]
    assert cls._retry_after_seconds == 120.0

    calls.clear()
    try:
        manager.download()
    except TradingAppException as exc:
        assert "temporarily throttled" in str(exc).lower() or "recently failed" in str(exc).lower()
    else:
        raise AssertionError("second call should be blocked by cooldown")
    assert calls == []
