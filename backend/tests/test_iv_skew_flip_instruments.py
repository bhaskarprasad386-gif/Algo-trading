from datetime import datetime, timedelta, timezone
import hashlib
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


def test_primary_master_is_used_without_shared_cache_or_alternate_request(monkeypatch):
    rows = valid_rows()
    monkeypatch.setattr(loader, "_write_snapshot", lambda rows: None)
    shared_rows = [{"symbol": "BOX-SPREAD-SHARED-CACHE-SENTINEL"}]
    FakeMaster._cached_instruments = shared_rows
    FakeMaster._cached_snapshot_date = "2000-01-01"
    master = FakeMaster()

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        if url != loader.PRIMARY_MASTER_URL:
            raise AssertionError("alternate host must not be requested")
        return Response()

    monkeypatch.setattr(loader.requests, "get", fake_get)
    assert loader.download_iv_instruments(master) == rows
    assert master.instruments == rows
    assert master._loaded is True
    assert FakeMaster._cached_instruments is shared_rows
    assert FakeMaster._cached_snapshot_date == "2000-01-01"
    assert calls == [(loader.PRIMARY_MASTER_URL, (5, 20))]


def test_alternate_official_master_is_validated_without_mutating_shared_cache(monkeypatch):
    rows = valid_rows()
    monkeypatch.setattr(loader, "_write_snapshot", lambda rows: None)
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
        if url == loader.PRIMARY_MASTER_URL:
            raise TimeoutError("primary host timeout")
        return Response()

    monkeypatch.setattr(loader.requests, "get", fake_get)
    result = loader.download_iv_instruments(master)

    assert result == rows
    assert master.instruments == rows
    assert master._loaded is True
    assert master._symbol_exchange_index is None
    assert FakeMaster._cached_instruments is shared_rows
    assert FakeMaster._cached_snapshot_date == "2000-01-01"
    assert calls == [
        (loader.PRIMARY_MASTER_URL, (5, 20)),
        (loader.ALTERNATE_MASTER_URL, (5, 20)),
    ]


def test_alternate_master_rejects_missing_nse_index_token(monkeypatch):
    master = FakeMaster(error=TimeoutError("primary host timeout"))
    rows = [row for row in valid_rows() if row["name"] != "BANKNIFTY" or row["exch_seg"] != "NSE"]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    monkeypatch.setattr(loader.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="no recent integrity-checked IV snapshot"):
        loader.download_iv_instruments(master)


def test_alternate_master_rejects_non_list_payload(monkeypatch):
    master = FakeMaster(error=TimeoutError("primary host timeout"))

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"instruments": valid_rows()}

    monkeypatch.setattr(loader.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="no recent integrity-checked IV snapshot"):
        loader.download_iv_instruments(master)


def test_recent_validated_snapshot_is_used_without_mutating_shared_cache(monkeypatch, tmp_path):
    rows = valid_rows()
    shared_rows = [{"symbol": "BOX-SPREAD-SHARED-CACHE-SENTINEL"}]
    FakeMaster._cached_instruments = shared_rows
    FakeMaster._cached_snapshot_date = "2000-01-01"
    cache = tmp_path / "iv_instrument_master_cache.json"
    cache.write_text(json.dumps({
        "schema_version": loader.CACHE_SCHEMA_VERSION,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest(),
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
        "schema_version": loader.CACHE_SCHEMA_VERSION,
        "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat(),
        "content_sha256": hashlib.sha256(json.dumps(valid_rows(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest(),
        "instruments": valid_rows(),
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "_cache_path", lambda: cache)
    monkeypatch.setattr(
        loader.requests, "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("alternate timeout")),
    )
    master = FakeMaster(error=TimeoutError("primary timeout"))

    with pytest.raises(RuntimeError, match="no recent integrity-checked IV snapshot"):
        loader.download_iv_instruments(master)


def test_total_master_failure_exposes_both_source_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(loader, "_cache_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(
        loader.requests, "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("alternate host timed out")),
    )
    master = FakeMaster(error=TimeoutError("primary host timed out"))

    with pytest.raises(RuntimeError) as exc:
        loader.download_iv_instruments(master)

    message = str(exc.value)
    assert "primary Angel One source failed [TimeoutError: primary host timed out]" in message
    assert "alternate Angel One source failed [TimeoutError: alternate host timed out]" in message
    assert "no recent integrity-checked IV snapshot" in message



def test_snapshot_content_tampering_is_rejected(monkeypatch, tmp_path):
    rows = valid_rows()
    cache = tmp_path / "iv_snapshot.json"
    # Point the isolated writer at this temporary IV-owned cache file.
    monkeypatch.setattr(loader, "_cache_path", lambda: cache)
    loader._write_snapshot(rows)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["instruments"][0]["token"] = "tampered-token"
    cache.write_text(json.dumps(payload), encoding="utf-8")
    assert loader._read_recent_snapshot() is None


def test_malformed_master_row_is_not_silently_dropped():
    rows = valid_rows() + [{"symbol": "BROKEN", "exch_seg": "NSE"}]
    with pytest.raises(ValueError, match="malformed or incomplete"):
        loader._validate_rows(rows)


def test_iv_snapshot_path_is_not_box_spread_cache(monkeypatch):
    monkeypatch.delenv("IV_INSTRUMENT_MASTER_CACHE_PATH", raising=False)
    monkeypatch.setenv("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", "/tmp/box-only-cache.json")
    assert loader._cache_path().name == "iv_instrument_master_cache.json"
    assert "box-only-cache" not in str(loader._cache_path())


@pytest.mark.parametrize("field,value", [
    ("symbol", None),
    ("exch_seg", None),
    ("token", None),
    ("token", True),
    ("token", "NaN"),
])
def test_master_rejects_null_boolean_and_non_numeric_identifiers(field, value):
    rows = valid_rows()
    rows[0][field] = value
    with pytest.raises(ValueError, match="malformed or incomplete"):
        loader._validate_rows(rows)


@pytest.mark.parametrize("field,value", [
    ("expiry", "not-a-date"),
    ("strike", "NaN"),
    ("strike", "0"),
    ("lotsize", "1.5"),
    ("lotsize", "0"),
])
def test_master_rejects_invalid_required_option_contract_fields(field, value):
    rows = valid_rows()
    rows[0][field] = value
    with pytest.raises(ValueError, match="missing complete NFO options"):
        loader._validate_rows(rows)
