import json
from datetime import datetime, timezone

import pytest
import requests

from app.core.exceptions import TradingAppException
from app.market_data.instruments import InstrumentMaster


@pytest.fixture
def isolated_master_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("INSTRUMENT_MASTER_CACHE_PATH", str(tmp_path / "instrument-master.json"))
    InstrumentMaster._cached_instruments = None
    InstrumentMaster._cached_snapshot_date = None
    InstrumentMaster._last_download_attempt_at = 0.0
    InstrumentMaster._last_download_error = None
    yield tmp_path / "instrument-master.json"
    InstrumentMaster._cached_instruments = None
    InstrumentMaster._cached_snapshot_date = None
    InstrumentMaster._last_download_attempt_at = 0.0
    InstrumentMaster._last_download_error = None


class Response:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self.rows


def test_successful_download_is_atomically_persisted(isolated_master_cache, monkeypatch):
    rows = [{"symbol": "ABC-EQ", "exch_seg": "NSE", "token": "123"}]
    monkeypatch.setattr("app.market_data.instruments.requests.get", lambda *a, **k: Response(rows))

    master = InstrumentMaster()
    assert master.download() == rows
    payload = json.loads(isolated_master_cache.read_text(encoding="utf-8"))
    assert payload["instruments"] == rows
    assert datetime.fromisoformat(payload["fetched_at"]).tzinfo is not None


def test_recent_disk_snapshot_is_used_when_provider_times_out(isolated_master_cache, monkeypatch):
    rows = [{"symbol": "ABC-EQ", "exch_seg": "NSE", "token": "123"}]
    isolated_master_cache.write_text(
        json.dumps({
            "schema_version": 1,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "instruments": rows,
        }),
        encoding="utf-8",
    )

    def timeout(*args, **kwargs):
        raise requests.ReadTimeout("provider timeout")

    monkeypatch.setattr("app.market_data.instruments.requests.get", timeout)
    master = InstrumentMaster()
    assert master.download(force=True) == rows
    assert master.get_token("ABC-EQ", "NSE") == "123"


def test_stale_disk_snapshot_is_not_used(isolated_master_cache, monkeypatch):
    rows = [{"symbol": "ABC-EQ", "exch_seg": "NSE", "token": "123"}]
    old = datetime.fromtimestamp(0, timezone.utc).isoformat()
    isolated_master_cache.write_text(
        json.dumps({"schema_version": 1, "fetched_at": old, "instruments": rows}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.market_data.instruments.requests.get",
        lambda *a, **k: (_ for _ in ()).throw(requests.ReadTimeout("provider timeout")),
    )

    master = InstrumentMaster()
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadError"):
        master.download(force=True)
