import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import TradingAppException
from app.market_data import instruments as instruments_module
from app.market_data.instruments import InstrumentMaster


@pytest.fixture
def isolated_master_cache(monkeypatch, tmp_path):
    cls = InstrumentMaster
    monkeypatch.setenv("INSTRUMENT_MASTER_CACHE_PATH", str(tmp_path / "instrument-master.json"))
    with cls._cache_lock:
        cls._cached_instruments = None
        cls._cached_snapshot_date = None
        cls._last_download_attempt_at = 0.0
        cls._last_download_error = None
        cls._cached_disk_snapshot_fetched_at = None
    yield tmp_path / "instrument-master.json"
    with cls._cache_lock:
        cls._cached_instruments = None
        cls._cached_snapshot_date = None
        cls._last_download_attempt_at = 0.0
        cls._last_download_error = None
        cls._cached_disk_snapshot_fetched_at = None


def _rows():
    return [{"symbol": "NIFTY", "exch_seg": "NSE", "token": "99926000"}]


def _write_snapshot(path, *, fetched_at=None, rows=None):
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "fetched_at": (fetched_at or datetime.now(timezone.utc)).isoformat(),
            "instruments": rows or _rows(),
        }),
        encoding="utf-8",
    )


def test_successful_official_download_is_atomically_persisted(isolated_master_cache, monkeypatch):
    rows = _rows()
    monkeypatch.setattr(instruments_module, "download_master_rows", lambda *args, **kwargs: rows)
    master = InstrumentMaster()

    assert master.download(force=True) == rows
    payload = json.loads(isolated_master_cache.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["instruments"] == rows
    assert datetime.fromisoformat(payload["fetched_at"]).tzinfo is not None


def test_recent_validated_disk_snapshot_is_used_on_provider_failure(isolated_master_cache, monkeypatch):
    rows = _rows()
    _write_snapshot(isolated_master_cache, rows=rows)
    monkeypatch.setattr(
        instruments_module,
        "download_master_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")),
    )
    master = InstrumentMaster()

    assert master.download(force=True) == rows
    assert master.resolve_index_token("NIFTY", "NSE") == "99926000"
    assert InstrumentMaster._cached_snapshot_date is None


def test_stale_disk_snapshot_is_rejected(isolated_master_cache, monkeypatch):
    _write_snapshot(
        isolated_master_cache,
        fetched_at=datetime.now(timezone.utc) - timedelta(hours=37),
    )
    monkeypatch.setattr(
        instruments_module,
        "download_master_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")),
    )
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadError"):
        InstrumentMaster().download(force=True)


def test_invalid_disk_snapshot_is_rejected(isolated_master_cache, monkeypatch):
    isolated_master_cache.write_text(
        json.dumps({
            "schema_version": 1,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "instruments": [{"symbol": "NIFTY", "exch_seg": "NSE", "token": ""}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        instruments_module,
        "download_master_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")),
    )
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadError"):
        InstrumentMaster().download(force=True)


def test_failed_download_is_throttled_without_repeated_provider_calls(isolated_master_cache, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(instruments_module, "download_master_rows", fail)
    master = InstrumentMaster()
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadError"):
        master.download(force=True)
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadCooldown"):
        master.download()
    # One failed refresh tries both official Angel One hosts; cooldown blocks a second refresh.
    assert len(calls) == 2



def test_disk_fallback_expires_after_36_hours_even_for_already_loaded_instance(
    isolated_master_cache, monkeypatch
):
    rows = _rows()
    _write_snapshot(isolated_master_cache, rows=rows)
    monkeypatch.setattr(
        instruments_module,
        "download_master_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")),
    )
    master = InstrumentMaster()
    assert master.download(force=True) == rows

    expired_at = datetime.now(timezone.utc) - timedelta(hours=37)
    InstrumentMaster._cached_disk_snapshot_fetched_at = expired_at
    master._disk_fallback_fetched_at = expired_at
    with pytest.raises(TradingAppException, match="InstrumentMasterDownloadCooldown"):
        master.search(tradingsymbol="NIFTY", exchange="NSE")
    assert master.instruments == []
