import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import TradingAppException
from app.market_data.instruments import InstrumentMaster


@pytest.fixture(autouse=True)
def reset_master_state():
    InstrumentMaster._cached_instruments = None
    InstrumentMaster._cached_snapshot_date = None
    InstrumentMaster._last_download_attempt_at = 0.0
    InstrumentMaster._last_download_error = None
    yield
    InstrumentMaster._cached_instruments = None
    InstrumentMaster._cached_snapshot_date = None
    InstrumentMaster._last_download_attempt_at = 0.0
    InstrumentMaster._last_download_error = None


def test_download_uses_recent_disk_snapshot_when_provider_fails(monkeypatch, tmp_path):
    rows = [{"symbol": "RELIANCE-EQ", "exch_seg": "NSE", "token": "2885"}]
    path = tmp_path / "instrument-master.json"
    path.write_text(
        json.dumps({"schema_version": 1, "fetched_at": datetime.now(timezone.utc).isoformat(), "instruments": rows}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INSTRUMENT_MASTER_CACHE_PATH", str(path))

    monkeypatch.setattr(
        "app.market_data.instruments.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")),
    )
    master = InstrumentMaster()
    assert master.download() == rows
    assert master.resolve_cash_token("RELIANCE") == "2885"


def test_download_rejects_stale_disk_snapshot(monkeypatch, tmp_path):
    rows = [{"symbol": "RELIANCE-EQ", "exch_seg": "NSE", "token": "2885"}]
    path = tmp_path / "instrument-master.json"
    stale = (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat()
    path.write_text(
        json.dumps({"schema_version": 1, "fetched_at": stale, "instruments": rows}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INSTRUMENT_MASTER_CACHE_PATH", str(path))
    monkeypatch.setattr(
        "app.market_data.instruments.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")),
    )
    master = InstrumentMaster()
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadError"):
        master.download()


def test_failed_download_is_throttled(monkeypatch, tmp_path):
    monkeypatch.setenv("INSTRUMENT_MASTER_CACHE_PATH", str(tmp_path / "missing.json"))
    calls = []

    def fail_request(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("provider timeout")

    monkeypatch.setattr("app.market_data.instruments.requests.get", fail_request)
    master = InstrumentMaster()
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadError"):
        master.download()
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadCooldown"):
        master.download()
    assert len(calls) == 1
