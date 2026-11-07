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
    return [
        {
            "symbol": "NIFTY12NOV26CE",
            "exch_seg": "NFO",
            "instrumenttype": "OPTIDX",
            "name": "NIFTY",
            "token": "101",
        },
        {
            "symbol": "BANKNIFTY26NOV26CE",
            "exch_seg": "NFO",
            "instrumenttype": "OPTIDX",
            "name": "BANKNIFTY",
            "token": "201",
        },
    ]


def test_primary_master_is_used_without_alternate_request(monkeypatch):
    rows = valid_rows()
    master = FakeMaster(result=rows)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("alternate host must not be requested")

    monkeypatch.setattr(loader.requests, "get", unexpected_request)
    assert loader.download_iv_instruments(master) is rows


def test_alternate_official_master_is_validated_and_cached(monkeypatch):
    rows = valid_rows()
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
    assert FakeMaster._cached_instruments == rows
    assert FakeMaster._cached_snapshot_date
    assert calls == [(loader.ALTERNATE_MASTER_URL, (5, 20))]


def test_alternate_master_rejects_missing_underlying_options(monkeypatch):
    master = FakeMaster(error=TimeoutError("primary host timeout"))

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return valid_rows()[:1]

    monkeypatch.setattr(loader.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match="BANKNIFTY"):
        loader.download_iv_instruments(master)


def test_alternate_master_rejects_non_list_payload(monkeypatch):
    master = FakeMaster(error=TimeoutError("primary host timeout"))

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"instruments": valid_rows()}

    monkeypatch.setattr(loader.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match="empty or invalid"):
        loader.download_iv_instruments(master)
