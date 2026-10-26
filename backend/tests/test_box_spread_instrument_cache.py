import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.market_data import box_spread_instrument_cache as cache_module
from app.market_data.box_spread_instrument_cache import download_box_spread_instruments


class FakeMaster:
    _cached_instruments = None
    _cached_snapshot_date = None

    def __init__(self, rows=None, error=None):
        self.instruments = []
        self._loaded = False
        self._symbol_exchange_index = None
        self.rows = rows or []
        self.error = error

    def download(self):
        if self.error:
            raise self.error
        self.instruments = self.rows
        self._loaded = True
        return self.rows


def _rows():
    return [{"symbol": "NIFTY26OCT25000CE", "exch_seg": "NFO", "token": "123"}]


def test_box_spread_writes_its_own_cache_after_success(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    rows = _rows()
    assert download_box_spread_instruments(FakeMaster(rows=rows)) == rows
    assert json.loads(path.read_text())["instruments"] == rows


def test_box_spread_uses_recent_own_cache_on_download_failure(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    rows = _rows()
    path.write_text(json.dumps({
        "schema_version": 1,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "instruments": rows,
    }))
    master = FakeMaster(error=RuntimeError("provider timeout"))
    assert download_box_spread_instruments(master) == rows
    assert master.instruments == rows
    assert master._loaded is True


def test_box_spread_tries_alternate_angel_one_host_before_disk_cache(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    rows = _rows()
    master = FakeMaster(error=RuntimeError("primary host timeout"))

    def fake_get(url, timeout):
        assert url == cache_module.ALTERNATE_MASTER_URL
        assert timeout == 20
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: rows,
        )

    monkeypatch.setattr(cache_module.requests, "get", fake_get)
    assert download_box_spread_instruments(master) == rows
    assert master.instruments == rows
    assert master._loaded is True
    assert json.loads(path.read_text())["instruments"] == rows
    assert FakeMaster._cached_instruments == rows


def test_box_spread_rejects_invalid_alternate_and_preserves_download_error(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    master = FakeMaster(error=RuntimeError("provider timeout"))

    monkeypatch.setattr(
        cache_module.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"not": "an instrument list"},
        ),
    )
    with pytest.raises(RuntimeError, match="provider timeout"):
        download_box_spread_instruments(master)


def test_box_spread_rejects_stale_cache_and_preserves_download_error(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    path.write_text(json.dumps({
        "schema_version": 1,
        "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat(),
        "instruments": _rows(),
    }))
    master = FakeMaster(error=RuntimeError("provider timeout"))
    monkeypatch.setattr(
        cache_module.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("alternate timeout")),
    )
    with pytest.raises(RuntimeError, match="provider timeout"):
        download_box_spread_instruments(master)
