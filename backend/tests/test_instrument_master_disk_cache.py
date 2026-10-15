import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import TradingAppException
from app.market_data.instruments import InstrumentMaster


@pytest.fixture(autouse=True)
def reset_master_state():
    InstrumentMaster._cached_instruments = None
    InstrumentMaster._cached_snapshot_date = None
    InstrumentMaster._last_failed_at_monotonic = None
    InstrumentMaster._last_download_error = None
    yield
    InstrumentMaster._cached_instruments = None
    InstrumentMaster._cached_snapshot_date = None
    InstrumentMaster._last_failed_at_monotonic = None
    InstrumentMaster._last_download_error = None


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = tmp_path / "instrument_master_cache.json"
    monkeypatch.setenv("ANGEL_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    return path


def sample_master():
    return [
        {"token": "101", "symbol": "ABC-EQ", "exch_seg": "NSE", "instrumenttype": "EQ"},
        {"token": "201", "symbol": "ABC30OCT26FUT", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30OCT2026"},
    ]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def write_cache(path, data=None, fetched_at=None):
    path.write_text(json.dumps({
        "schema_version": 1,
        "fetched_at": (fetched_at or datetime.now(timezone.utc)).isoformat(),
        "snapshot_date": "2026-10-15",
        "instruments": data if data is not None else sample_master(),
    }), encoding="utf-8")


def test_successful_download_writes_validated_disk_cache(monkeypatch, cache_path):
    monkeypatch.setattr(
        "app.market_data.instruments.requests.get",
        lambda *args, **kwargs: FakeResponse(sample_master()),
    )

    result = InstrumentMaster().download(force=True)

    assert len(result) == 2
    assert result[0]["token"] == "101"
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert saved["instruments"] == sample_master()
    assert "fetched_at" in saved


def test_network_failure_uses_fresh_validated_disk_cache(monkeypatch, cache_path):
    write_cache(cache_path)

    def fail_request(*args, **kwargs):
        raise TimeoutError("upstream timed out")

    monkeypatch.setattr("app.market_data.instruments.requests.get", fail_request)

    result = InstrumentMaster().download(force=True)

    assert result == sample_master()
    assert InstrumentMaster._cached_snapshot_date == "2026-10-15"


def test_stale_cache_is_rejected_and_download_failure_is_explicit(monkeypatch, cache_path):
    write_cache(cache_path, fetched_at=datetime.now(timezone.utc) - timedelta(hours=48))
    monkeypatch.setattr(
        "app.market_data.instruments.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )

    with pytest.raises(TradingAppException) as error:
        InstrumentMaster().download(force=True)

    assert error.value.name == "InstrumentMasterDownloadError"
    assert "no valid disk cache" in error.value.message


def test_malformed_upstream_payload_falls_back_to_valid_cache(monkeypatch, cache_path):
    write_cache(cache_path)
    monkeypatch.setattr(
        "app.market_data.instruments.requests.get",
        lambda *args, **kwargs: FakeResponse({"error": "upstream unavailable"}),
    )

    assert InstrumentMaster().download(force=True) == sample_master()


def test_invalid_cache_structure_is_not_used(monkeypatch, cache_path):
    write_cache(cache_path, data=[{"error": "not an instrument row"}])
    monkeypatch.setattr(
        "app.market_data.instruments.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )

    with pytest.raises(TradingAppException):
        InstrumentMaster().download(force=True)


def test_request_uses_bounded_connect_and_read_timeouts(monkeypatch, cache_path):
    captured = {}

    def request(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(sample_master())

    monkeypatch.setattr("app.market_data.instruments.requests.get", request)
    InstrumentMaster().download(force=True)

    assert captured["timeout"] == (5.0, 15.0)
