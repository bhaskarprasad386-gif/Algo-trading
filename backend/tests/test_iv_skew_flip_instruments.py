from datetime import datetime, timedelta, timezone
import json
from threading import Lock

import pytest

from app.scanner.iv_skew_flip import instruments as loader


class FakeMaster:
    _cache_lock = Lock()
    _cached_instruments = None
    _cached_snapshot_date = None

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.instruments = []
        self._loaded = False
        self._symbol_exchange_index = None

    def download(self):
        if self.error:
            raise self.error
        return self.result


def valid_rows():
    rows = []
    for underlying, token in (("NIFTY", "101"), ("BANKNIFTY", "201")):
        rows.append({
            "symbol": f"{underlying}12NOV26CE",
            "exch_seg": "NFO",
            "instrumenttype": "OPTIDX",
            "name": underlying,
            "expiry": "12NOV2026",
            "strike": "2500000",
            "lotsize": "25",
            "token": token,
        })
    rows.extend([
        {
            "symbol": "Nifty 50",
            "name": "NIFTY",
            "exch_seg": "NSE",
            "instrumenttype": "AMXIDX",
            "token": "99926000",
        },
        {
            "symbol": "Nifty Bank",
            "name": "BANKNIFTY",
            "exch_seg": "NSE",
            "instrumenttype": "AMXIDX",
            "token": "99926009",
        },
    ])
    return rows


def test_primary_master_is_used_without_alternate_request(monkeypatch):
    rows = valid_rows()
    master = FakeMaster(result=rows)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("alternate host must not be requested")

    monkeypatch.setattr(loader.requests, "get", unexpected_request)
    assert loader.download_iv_instruments(master) == rows


def test_alternate_official_master_is_validated_without_mutating_shared_cache(monkeypatch):
    rows = valid_rows()
    shared_rows = [{"symbol": "BOX-SPREAD-SHARED-CACHE-SENTINEL"}]
    FakeMaster._cached_instruments = shared_rows
    FakeMaster._cached_snapshot_date = "2000-01-01"
    master = FakeMaster(error=TimeoutError("primary host timeout"))

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr(loader.requests, "get", fake_get)
    result = loader.download_iv_instruments(master)

    assert result == rows
    assert master.instruments == rows
    assert master._loaded is True
    assert master._symbol_exchange_index is None
    assert FakeMaster._cached_instruments is shared_rows
    assert FakeMaster._cached_snapshot_date == "2000-01-01"
    assert calls == [(loader.ALTERNATE_MASTER_URL, (5, 20))]


def test_alternate_master_rejects_missing_nse_index_token(monkeypatch):
    master = FakeMaster(error=TimeoutError("primary host timeout"))
    rows = [row for row in valid_rows() if row["name"] != "BANKNIFTY" or row["exch_seg"] != "NSE"]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    monkeypatch.setattr(loader.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="no recent complete snapshot"):
        loader.download_iv_instruments(master)


def test_alternate_master_rejects_non_list_payload(monkeypatch):
    master = FakeMaster(error=TimeoutError("primary host timeout"))

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"instruments": valid_rows()}

    monkeypatch.setattr(loader.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="no recent complete snapshot"):
        loader.download_iv_instruments(master)


def test_recent_validated_snapshot_is_used_without_mutating_shared_cache(monkeypatch, tmp_path):
    rows = valid_rows()
    shared_rows = [{"symbol": "BOX-SPREAD-SHARED-CACHE-SENTINEL"}]
    FakeMaster._cached_instruments = shared_rows
    FakeMaster._cached_snapshot_date = "2000-01-01"
    cache = tmp_path / "box_spread_instrument_master_cache.json"
    cache.write_text(json.dumps({
        "schema_version": 1,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "instruments": rows,
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "_cache_path", lambda: cache)
    monkeypatch.setattr(
        loader.requests, "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("alternate timeout")),
    )
    master = FakeMaster(error=TimeoutError("primary timeout"))

    assert loader.download_iv_instruments(master) == rows
    assert master.instruments == rows
    assert master._loaded is True
    assert FakeMaster._cached_instruments is shared_rows
    assert FakeMaster._cached_snapshot_date == "2000-01-01"


def test_stale_or_incomplete_snapshot_is_never_used(monkeypatch, tmp_path):
    cache = tmp_path / "stale.json"
    cache.write_text(json.dumps({
        "schema_version": 1,
        "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat(),
        "instruments": valid_rows(),
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "_cache_path", lambda: cache)
    monkeypatch.setattr(
        loader.requests, "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("alternate timeout")),
    )
    master = FakeMaster(error=TimeoutError("primary timeout"))

    with pytest.raises(RuntimeError, match="no recent complete snapshot"):
        loader.download_iv_instruments(master)
