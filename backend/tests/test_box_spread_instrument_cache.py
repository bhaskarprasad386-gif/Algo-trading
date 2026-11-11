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


def _fail_alternate_host(monkeypatch):
    monkeypatch.setattr(
        cache_module.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("alternate timeout")),
    )


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
        "schema_version": 2,
        "content_sha256": cache_module._canonical_rows_digest(_rows()),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "instruments": rows,
    }))
    master = FakeMaster(error=RuntimeError("provider timeout"))
    _fail_alternate_host(monkeypatch)
    assert download_box_spread_instruments(master) == rows
    assert master.instruments == rows
    assert master._loaded is True


def test_box_spread_rejects_stale_cache_and_preserves_download_error(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    path.write_text(json.dumps({
        "schema_version": 2,
        "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat(),
        "content_sha256": cache_module._canonical_rows_digest(_rows()),
        "instruments": _rows(),
    }))
    master = FakeMaster(error=RuntimeError("provider timeout"))
    _fail_alternate_host(monkeypatch)
    with pytest.raises(RuntimeError, match="provider timeout"):
        download_box_spread_instruments(master)


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


def test_box_spread_reuses_fresh_verified_in_process_iv_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    rows = _rows()
    master = FakeMaster()
    master.instruments = rows
    master._loaded = True
    master._iv_snapshot_fetched_at = datetime.now(timezone.utc)
    master.download = lambda: (_ for _ in ()).throw(AssertionError("network download must not run"))
    assert download_box_spread_instruments(master) == rows
    assert json.loads(path.read_text())["instruments"] == rows


def test_box_spread_ignores_stale_in_process_iv_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    master = FakeMaster(error=RuntimeError("provider timeout"))
    master.instruments = _rows()
    master._loaded = True
    master._iv_snapshot_fetched_at = datetime.now(timezone.utc) - timedelta(hours=37)
    _fail_alternate_host(monkeypatch)
    with pytest.raises(RuntimeError, match="provider timeout"):
        download_box_spread_instruments(master)


def test_box_spread_streams_bounded_alternate_master_and_closes_response(monkeypatch):
    rows = _rows()
    payload = json.dumps(rows).encode("utf-8")
    calls = []
    class Response:
        closed = False
        def raise_for_status(self):
            pass
        def iter_content(self, chunk_size):
            assert chunk_size == cache_module.MASTER_CHUNK_BYTES
            yield payload
        def close(self):
            self.closed = True
    response = Response()
    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return response
    monkeypatch.setattr(cache_module.requests, "get", fake_get)
    assert cache_module._download_alternate_rows() == rows
    assert response.closed is True
    assert calls[0][0] == cache_module.ALTERNATE_MASTER_URL
    assert calls[0][1]["stream"] is True
    assert calls[0][1]["timeout"] == (10, 120)


def test_box_spread_streaming_rejects_oversized_payload(monkeypatch):
    class Response:
        closed = False
        def raise_for_status(self):
            pass
        def iter_content(self, chunk_size):
            yield b"x" * (cache_module.MASTER_MAX_BYTES + 1)
        def close(self):
            self.closed = True
    response = Response()
    monkeypatch.setattr(cache_module.requests, "get", lambda *args, **kwargs: response)
    with pytest.raises(ValueError, match="exceeded"):
        cache_module._download_alternate_rows()
    assert response.closed is True

def test_box_spread_cache_rejects_tampered_content_digest(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    rows = _rows()
    cache_module._write_cache(rows)
    payload = json.loads(path.read_text())
    payload["instruments"][0]["symbol"] = "TAMPERED"
    path.write_text(json.dumps(payload))
    assert cache_module._read_cache() is None


def test_box_spread_cache_rejects_legacy_schema_without_digest(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    path.write_text(json.dumps({
        "schema_version": 1,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "instruments": _rows(),
    }))
    assert cache_module._read_cache() is None


def test_box_spread_cache_rejects_malformed_rows(monkeypatch, tmp_path):
    path = tmp_path / "box-cache.json"
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", str(path))
    path.write_text(json.dumps({
        "schema_version": 2,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": cache_module._canonical_rows_digest([{"symbol": "X"}]),
        "instruments": [{"symbol": "X"}],
    }))
    assert cache_module._read_cache() is None
